"""Similar-preview, explain breakdown, similarity settings, and score sanity."""
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
    # contributions add up to the overall — the UI can show math that checks out
    assert abs(sum(body["contributions"].values()) - body["overall"]) < 0.001
    assert set(body["params"]) == {"a", "b"}
    assert body["shared"]["parameters"] == ["invoice_id", "max_results"]
    assert body["recommendations"]
    fields = {rec["field"] for rec in body["recommendations"]}
    assert "parameters" in fields                        # shared-params rule fired


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


async def test_global_threshold_setting(client):
    su = await login(client)
    await make_product(client, su, "p1")
    await make_product(client, su, "p2")
    assert (await client.get("/v1/products/p1/settings", headers=su)).json() == \
        {"similarity_threshold": 0.5}
    # super admin sets it ONCE -> every product reports the same value
    r = await client.put("/v1/products/p1/settings",
                         json={"similarity_threshold": 0.7}, headers=su)
    assert r.status_code == 204
    for pk in ("p1", "p2"):
        assert (await client.get(f"/v1/products/{pk}/settings",
                                 headers=su)).json()["similarity_threshold"] == 0.7
        assert (await client.get(f"/v1/products/{pk}/entities/reports/duplicates",
                                 headers=su)).json()["threshold"] == 0.7
    # product admins cannot change a registry-wide setting
    from .conftest import make_user
    await make_user(client, su, "alice@co.com")
    await client.put("/v1/products/p1/members",
                     json={"email": "alice@co.com", "role": "admin"}, headers=su)
    alice = await login(client, "alice@co.com", "secret1")
    assert (await client.put("/v1/products/p1/settings",
                             json={"similarity_threshold": 0.6}, headers=alice)).status_code == 403
    # bounds enforced
    assert (await client.put("/v1/products/p1/settings",
                             json={"similarity_threshold": 1.5}, headers=su)).status_code == 422


async def test_suggestions_on_flagged_draft(client):
    """A flagged lookalike gets safe 'use this instead' suggestions: valid names,
    unique across ALL products, plus a concrete description tip."""
    su = await login(client)
    await make_product(client, su, "billing")
    await make_product(client, su, "shipping")
    mk = lambda n, d, p=None: {"name": n, "description": d,
        "input_schema": {"type": "object", "properties": p or {}}}
    await client.post("/v1/products/billing/entities", headers=su, json={"type": "tool",
        "payload": mk("get_invoice", "Fetch an invoice by its ID.",
                      {"invoice_id": {"type": "string"}})})
    await client.post("/v1/products/shipping/entities", headers=su, json={"type": "tool",
        "payload": mk("get_invoice_pdf", "Fetch an invoice document.", {})})
    # a draft that duplicates billing/get_invoice, with one genuinely distinct angle
    draft = {"type": "tool", "payload": mk(
        "get_invoice", "Fetch an invoice by its ID from the archived ledger.",
        {"invoice_id": {"type": "string"}, "archive_year": {"type": "integer"}})}
    r = await client.post("/v1/products/shipping/entities/similar-preview",
                          json=draft, headers=su)
    body = r.json()
    s = body["suggestions"]
    assert s and s["names"], body
    import re
    existing = {"get_invoice", "get_invoice_pdf"}
    for item in s["names"]:
        assert re.match(r"^[a-zA-Z][a-zA-Z0-9_-]{0,127}$", item["name"])
        assert item["name"].lower() not in existing   # collision-free across products
        assert item["title"][0].isupper()             # human title provided
        assert 0 <= item["new_overall"] <= 1          # predicted OVERALL after rename
    assert s["title_suggestion"]                      # per-section: applyable title
    assert 0 <= s["current_name_match"] <= 1
    # distinct angle surfaces in both a name and the tip
    assert any("archive" in i["name"] or "ledger" in i["name"] for i in s["names"])
    assert "archive" in s["description_tip"] or "ledger" in s["description_tip"]
    # a clearly novel draft gets NO suggestions block
    r = await client.post("/v1/products/shipping/entities/similar-preview",
        json={"type": "tool", "payload": mk("rotate_logs",
              "Rotate and compress server log files weekly.", {})}, headers=su)
    assert r.json()["suggestions"] is None


async def test_description_tip_when_nothing_distinct(client):
    from app.suggestions import description_tip
    a = {"name": "fetch_invoice", "description": "Fetch an invoice by ID.",
         "input_schema": {"type": "object", "properties": {}}}
    b = {"name": "get_invoice", "description": "Fetch an invoice by its ID.",
         "input_schema": {"type": "object", "properties": {}}}
    tip = description_tip(a, b)
    assert "different" in tip and "get_invoice" in tip


def test_suggestions_never_repeat_name_tokens():
    """Applying a suggestion then re-checking must not stack the same qualifier."""
    from app.suggestions import suggest_names
    draft = {"name": "fetch_invoice_retrieve",
             "description": "Retrieve the freight invoice for a shipment by its identifier.",
             "input_schema": {"type": "object", "properties": {}}}
    other = {"name": "get_invoice", "description": "Fetch a single invoice by its ID.",
             "input_schema": {"type": "object", "properties": {}}}
    for n in suggest_names(draft, other, "shipping", {"get_invoice"}):
        toks = n.lower().split("_")
        assert len(toks) == len(set(toks)), n     # no token appears twice


async def test_overlap_scoping_per_product_and_global(client):
    """A product page never shows other products' unrelated pairs; the global
    report (Products page) shows everything."""
    su = await login(client)
    mk = lambda n, d: {"name": n, "description": d,
                       "input_schema": {"type": "object", "properties": {}}}
    for pk in ("alpha", "beta"):
        await make_product(client, su, pk)
    await client.post("/v1/products/beta/entities", headers=su,
        json={"type": "tool", "payload": mk("send_alert", "Send an alert notification to a user.")})
    await client.post("/v1/products/beta/entities", headers=su,
        json={"type": "tool", "payload": mk("notify_user", "Send an alert notification to a user.")})
    await client.post("/v1/products/alpha/entities", headers=su,
        json={"type": "tool", "payload": mk("rotate_logs", "Rotate server log files weekly.")})
    rep = (await client.get("/v1/products/alpha/entities/reports/duplicates",
                            headers=su)).json()
    assert rep["pairs"] == []                               # alpha never sees beta's pair
    rep = (await client.get("/v1/products/beta/entities/reports/duplicates",
                            headers=su)).json()
    assert len(rep["pairs"]) == 1
    rep = (await client.get("/v1/reports/duplicates", headers=su)).json()
    assert any({p["a"]["name"], p["b"]["name"]} == {"send_alert", "notify_user"}
               for p in rep["pairs"])


def test_description_suggestion_idempotent_and_hint():
    """Never re-append the differentiator; when nothing resolves, say what would."""
    from app.suggestions import build_suggestions
    other = {"name": "get_invoice", "description": "Fetch a single invoice by its ID.",
             "input_schema": {"type": "object", "properties": {}}}
    # already differentiated -> no re-offer
    draft = {"name": "fetch_invoice",
             "description": "Retrieve freight invoices. Unlike get_invoice, this is "
                            "specifically about freight, shipment.",
             "input_schema": {"type": "object", "properties": {}}}
    s = build_suggestions(draft, other, "shipping", {"get_invoice"}, True, 0.5)
    assert s["description_suggestion"] is None
