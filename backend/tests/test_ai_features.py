"""Similar-preview, explain breakdown, AI config gating, and Bifrost-backed
generation/explanation (LLM mocked — no network in tests)."""
import copy

import pytest

from .conftest import TOOL, login, make_product


async def seed(client, su):
    mk = lambda name, desc, params=None: {"name": name, "description": desc,
        "input_schema": {"type": "object", "properties": params or {}}}
    await make_product(client, su, "billing")
    await make_product(client, su, "shipping")
    r = await client.post("/v1/products/billing/entities", headers=su, json={"type": "tool",
        "payload": mk("get_invoice", "Fetch an invoice by its ID.",
                      {"invoice_id": {"type": "string"}, "max_results": {"type": "integer"}})})
    await client.post("/v1/products/shipping/entities", headers=su, json={"type": "tool",
        "payload": mk("fetch_invoice", "Fetch an invoice by ID for a customer.",
                      {"invoice_id": {"type": "string"}, "max_results": {"type": "integer"}})})
    return r.json()


async def test_similar_preview_on_draft(client):
    su = await login(client)
    await seed(client, su)
    draft = {"type": "tool", "payload": {
        "name": "get_invoice_details", "description": "Fetch an invoice by its ID.",
        "input_schema": {"type": "object", "properties": {"invoice_id": {"type": "string"}}}}}
    r = await client.post("/v1/products/billing/entities/similar-preview",
                          json=draft, headers=su)
    body = r.json()
    assert body["matches"][0]["score"] >= 0.6            # warns BEFORE saving
    assert body["top_explain"] is not None
    ex = body["top_explain"]
    assert ex["subscores"]["description"] >= 0.8
    assert "invoice_id" in ex["shared"]["parameters"]
    assert "invoice" in ex["shared"]["terms"]
    assert any(rec["severity"] == "high" for rec in ex["recommendations"])
    # empty draft: no crash, no matches
    r = await client.post("/v1/products/billing/entities/similar-preview",
                          json={"type": "tool", "payload": {}}, headers=su)
    assert r.json() == {"matches": [], "top_explain": None, "threshold": 0.5}


async def test_explain_pair_endpoint(client):
    su = await login(client)
    e = await seed(client, su)
    other_id = (await client.get("/v1/products/shipping/entities", headers=su)).json()[0]["id"]
    r = await client.get(f"/v1/products/billing/entities/{e['id']}/explain/{other_id}",
                         headers=su)
    body = r.json()
    assert set(body["subscores"]) == {"name", "description", "parameters"}
    assert body["shared"]["parameters"] == ["invoice_id", "max_results"]
    assert body["recommendations"]
    fields = {rec["field"] for rec in body["recommendations"]}
    assert "parameters" in fields                        # shared-params rule fired


async def test_ai_config_rbac_and_status(client):
    su = await login(client)
    await seed(client, su)
    # not configured -> status false, generate blocked with a clear message
    st = (await client.get("/v1/products/billing/ai-config/status", headers=su)).json()
    assert st == {"configured": False, "model": None}
    r = await client.post("/v1/products/billing/entities/ai/generate",
                          json={"type": "tool", "payload": TOOL}, headers=su)
    assert r.status_code == 400 and "Bifrost" in r.json()["detail"]
    # super admin sets config; stored encrypted; member can see status but not the key
    await client.put("/v1/products/billing/ai-config", headers=su,
                     json={"base_url": "http://localhost:8080/v1", "api_key": "vk-secret",
                           "model": "anthropic/claude-sonnet-4-5"})
    got = (await client.get("/v1/products/billing/ai-config", headers=su)).json()
    assert got["api_key"] == "vk-secret"
    from app.db import session_factory
    from app.models import AiConfig
    from sqlalchemy import select
    async with session_factory()() as db:
        row = (await db.execute(select(AiConfig))).scalars().first()
        assert "vk-secret" not in row.config_enc         # encrypted at rest
    st = (await client.get("/v1/products/billing/ai-config/status", headers=su)).json()
    assert st["configured"] is True
    # product admin cannot read the config (key stays super-admin-only)
    from .conftest import make_user
    await make_user(client, su, "alice@co.com")
    await client.put("/v1/products/billing/members",
                     json={"email": "alice@co.com", "role": "admin"}, headers=su)
    alice = await login(client, "alice@co.com", "secret1")
    assert (await client.get("/v1/products/billing/ai-config", headers=alice)).status_code == 403
    assert (await client.get("/v1/products/billing/ai-config/status",
                             headers=alice)).json()["configured"] is True


async def test_ai_generate_and_explain_with_mocked_llm(client, monkeypatch):
    su = await login(client)
    e = await seed(client, su)
    await client.put("/v1/products/billing/ai-config", headers=su,
                     json={"base_url": "http://bifrost.local/v1", "api_key": "vk", "model": "m"})
    calls = {}

    async def fake_complete(self, system, user, max_tokens=1024):
        calls["system"] = system; calls["user"] = user
        if "STRICT JSON" in system:
            return ('Here you go:\n```json\n{"description": "Fetch a billing invoice by its '
                    'unique ID from the billing ledger.", "title": "Get billing invoice", '
                    '"param_descriptions": {"invoice_id": "Unique billing invoice ID"}}\n```')
        return "These are near-duplicates; keep billing/get_invoice as canonical."

    from app.llm import LLMClient
    monkeypatch.setattr(LLMClient, "complete", fake_complete)
    # generate: suggestion parsed from fenced JSON, similar tools fed to the prompt
    r = await client.post("/v1/products/billing/entities/ai/generate",
                          json={"type": "tool", "payload": {
                              "name": "get_invoice_details",
                              "description": "Fetch an invoice by its ID.",
                              "input_schema": {"type": "object", "properties": {
                                  "invoice_id": {"type": "string"}}}}}, headers=su)
    body = r.json()
    assert body["suggestion"]["description"].startswith("Fetch a billing invoice")
    assert body["suggestion"]["param_descriptions"]["invoice_id"]
    assert "fetch_invoice" in calls["user"]              # similar tools included in prompt
    # ai explain
    other_id = (await client.get("/v1/products/shipping/entities", headers=su)).json()[0]["id"]
    r = await client.post(f"/v1/products/billing/entities/{e['id']}/explain/{other_id}/ai",
                          headers=su)
    assert "canonical" in r.json()["analysis"]


async def test_scores_never_negative_and_no_mirrored_pairs(client):
    su = await login(client)
    await make_product(client, su, "p1")
    mk = lambda n, d: {"name": n, "description": d,
                       "input_schema": {"type": "object", "properties": {}}}
    for n, d in (("alpha_tool", "Rotate compressed log archives weekly."),
                 ("beta_tool", "Send festive greeting cards to customers."),
                 ("gamma_tool", "Rotate compressed log archives weekly.")):
        await client.post("/v1/products/p1/entities", headers=su,
                          json={"type": "tool", "payload": mk(n, d)})
    # similar: scores clamped at 0 even for unrelated tools
    eid = (await client.get("/v1/products/p1/entities", headers=su)).json()[0]["id"]
    sim = (await client.get(f"/v1/products/p1/entities/{eid}/similar", headers=su)).json()
    assert all(s["score"] >= 0 for s in sim)
    # duplicates at hostile threshold: floored, no mirrored/self pairs
    rep = (await client.get("/v1/products/p1/entities/reports/duplicates",
                            params={"threshold": -5}, headers=su)).json()
    seen = set()
    for p in rep["pairs"]:
        assert p["a"]["id"] != p["b"]["id"]                      # no self pairs
        key = frozenset((p["a"]["id"], p["b"]["id"]))
        assert key not in seen                                    # no mirrored pairs
        seen.add(key)
        assert p["score"] >= 0


async def test_product_threshold_setting(client):
    su = await login(client)
    await make_product(client, su, "p1")
    # default 50%
    assert (await client.get("/v1/products/p1/settings", headers=su)).json() == \
        {"similarity_threshold": 0.5}
    # admin can change it; duplicates report uses it as its default
    r = await client.put("/v1/products/p1/settings",
                         json={"similarity_threshold": 0.7}, headers=su)
    assert r.status_code == 204
    rep = (await client.get("/v1/products/p1/entities/reports/duplicates",
                            headers=su)).json()
    assert rep["threshold"] == 0.7
    # bounds enforced
    assert (await client.put("/v1/products/p1/settings",
                             json={"similarity_threshold": 1.5}, headers=su)).status_code == 422


async def test_prompt_override_from_config(client, monkeypatch):
    su = await login(client)
    await make_product(client, su, "p1")
    await client.put("/v1/products/p1/ai-config", headers=su,
                     json={"base_url": "http://b/v1", "api_key": "vk", "model": "m",
                           "generate_prompt": "CUSTOM PROMPT FROM BIFROST STORE. STRICT JSON."})
    seen = {}

    async def fake_complete(self, system, user, max_tokens=1024):
        seen["system"] = system
        return '{"description": "d", "title": "t", "param_descriptions": {}}'

    from app.llm import LLMClient
    monkeypatch.setattr(LLMClient, "complete", fake_complete)
    await client.post("/v1/products/p1/entities/ai/generate", headers=su,
                      json={"type": "tool", "payload": {"name": "x", "description": "y"}})
    assert seen["system"].startswith("CUSTOM PROMPT FROM BIFROST STORE")
