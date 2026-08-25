"""Entities, audience overlays, validation pipeline, dry-run, versions, rollback."""
import copy

from .conftest import TOOL, login, make_product


async def setup(client):
    su = await login(client)
    await make_product(client, su, "billing")
    for aud in ("internal", "intermediate"):
        await client.post("/v1/products/billing/audiences",
                          json={"key": aud}, headers=su)
    return su


async def test_create_and_resolve_default_audience(client):
    su = await setup(client)
    r = await client.post("/v1/products/billing/entities",
                          json={"type": "tool", "payload": TOOL}, headers=su)
    assert r.status_code == 201
    views = r.json()["resolved"]
    assert set(views) == {"external", "internal", "intermediate"}
    ext = views["external"]
    assert ext["enabled"] and ext["spec"]["description"] == TOOL["description"]
    assert ext["spec"]["input_schema"]["required"] == ["invoice_id"]


async def test_overlay_add_modify_hide_pin(client):
    su = await setup(client)
    payload = copy.deepcopy(TOOL)
    payload["audiences"] = {
        "internal": {"overrides": {
            "description": "Fetch invoice incl. raw ledger refs.",
            "parameters": {
                "add": {"include_ledger": {"type": "boolean", "default": False}},
                "modify": {"invoice_id": {"description": "ID or internal ledger ref"}},
            },
        }},
        "external": {"overrides": {
            "parameters": {"hide": {"max_results": {"pin": 25}}},
        }},
        "intermediate": {"enabled": False},
    }
    r = await client.post("/v1/products/billing/entities",
                          json={"type": "tool", "payload": payload}, headers=su)
    assert r.status_code == 201, r.text
    v = r.json()["resolved"]
    internal, external, mid = v["internal"], v["external"], v["intermediate"]
    # internal: added + modified params visible, custom description
    assert "include_ledger" in internal["spec"]["input_schema"]["properties"]
    assert internal["spec"]["input_schema"]["properties"]["invoice_id"]["description"] \
        == "ID or internal ledger ref"
    assert internal["spec"]["description"].startswith("Fetch invoice incl.")
    # external: max_results hidden and pinned server-side
    assert "max_results" not in external["spec"]["input_schema"]["properties"]
    assert external["pins"] == {"max_results": 25}
    # intermediate: disabled entirely
    assert mid == {"enabled": False}


async def test_validation_field_level_errors(client):
    su = await setup(client)
    payload = copy.deepcopy(TOOL)
    payload["audiences"] = {"external": {"overrides": {"parameters": {
        "hide": {"max_results": {"pin": "not-an-int"}},      # pin violates param schema
        "modify": {"ghost": {"description": "x"}},           # unknown param
    }}}}
    r = await client.post("/v1/products/billing/entities",
                          json={"type": "tool", "payload": payload}, headers=su)
    assert r.status_code == 422
    errors = r.json()["detail"]["errors"]
    codes = {e["code"] for e in errors}
    assert codes == {"invalid_pin", "unknown_param"}
    paths = {e["path"] for e in errors}
    assert "audiences/external/parameters/hide/max_results/pin" in paths
    # nothing committed
    assert (await client.get("/v1/products/billing/entities", headers=su)).json() == []


async def test_hide_without_pin_not_constructible(client):
    su = await setup(client)
    payload = copy.deepcopy(TOOL)
    payload["audiences"] = {"external": {"overrides": {"parameters": {
        "hide": {"max_results": {}}}}}}                       # no pin -> shape error
    r = await client.post("/v1/products/billing/entities",
                          json={"type": "tool", "payload": payload}, headers=su)
    assert r.status_code == 422
    assert any(e["code"] == "schema" for e in r.json()["detail"]["errors"])


async def test_unknown_audience_rejected(client):
    su = await setup(client)
    payload = copy.deepcopy(TOOL)
    payload["audiences"] = {"partner": {"enabled": True}}
    r = await client.post("/v1/products/billing/entities",
                          json={"type": "tool", "payload": payload}, headers=su)
    assert r.status_code == 422
    assert r.json()["detail"]["errors"][0]["code"] == "unknown_audience"


async def test_bad_base_payload_rejected(client):
    su = await setup(client)
    for bad in ({"description": "no name"},
                {"name": "x y z!", "description": "bad tool name"},
                {"name": "ok", "description": ""},
                {"name": "ok", "description": "d", "input_schema": {"type": 123}}):
        r = await client.post("/v1/products/billing/entities",
                              json={"type": "tool", "payload": bad}, headers=su)
        assert r.status_code == 422, bad


async def test_dry_run_previews_without_commit(client):
    su = await setup(client)
    payload = copy.deepcopy(TOOL)
    payload["audiences"] = {"external": {"overrides": {"parameters": {
        "hide": {"max_results": {"pin": 10}}}}}}
    r = await client.post("/v1/products/billing/entities/dry-run",
                          json={"type": "tool", "payload": payload}, headers=su)
    body = r.json()
    assert body["valid"] and body["resolved"]["external"]["pins"] == {"max_results": 10}
    assert (await client.get("/v1/products/billing/entities", headers=su)).json() == []
    # invalid dry run returns the same field-level errors as a real save would
    payload["audiences"]["external"]["overrides"]["parameters"]["hide"]["max_results"]["pin"] = -5
    r = await client.post("/v1/products/billing/entities/dry-run",
                          json={"type": "tool", "payload": payload}, headers=su)
    assert not r.json()["valid"] and r.json()["errors"][0]["code"] == "invalid_pin"


async def test_duplicate_name_conflict(client):
    su = await setup(client)
    await client.post("/v1/products/billing/entities",
                      json={"type": "tool", "payload": TOOL}, headers=su)
    r = await client.post("/v1/products/billing/entities",
                          json={"type": "tool", "payload": TOOL}, headers=su)
    assert r.status_code == 409


async def test_versions_and_rollback(client):
    su = await setup(client)
    e = (await client.post("/v1/products/billing/entities",
                           json={"type": "tool", "payload": TOOL}, headers=su)).json()
    v2 = copy.deepcopy(TOOL)
    v2["description"] = "Updated description v2."
    await client.put(f"/v1/products/billing/entities/{e['id']}",
                     json={"payload": v2, "note": "tweak"}, headers=su)
    versions = (await client.get(f"/v1/products/billing/entities/{e['id']}/versions",
                                 headers=su)).json()
    assert [v["version"] for v in versions] == [2, 1]
    rolled = (await client.post(f"/v1/products/billing/entities/{e['id']}/rollback/1",
                                headers=su)).json()
    assert rolled["version"] == 3                       # undo is a NEW version
    assert rolled["payload"]["description"] == TOOL["description"]


async def test_agent_entity_type_supported(client):
    su = await setup(client)
    agent = {"name": "invoice_reconciler", "description": "Reconciles invoices nightly.",
             "card": {"skills": ["reconcile"], "endpoint": "https://a2a.example/agent"}}
    r = await client.post("/v1/products/billing/entities",
                          json={"type": "agent", "payload": agent}, headers=su)
    assert r.status_code == 201
    assert r.json()["resolved"]["external"]["spec"]["card"]["skills"] == ["reconcile"]


async def test_delete_removes_from_list_and_manifest(client):
    su = await setup(client)
    e = (await client.post("/v1/products/billing/entities",
                           json={"type": "tool", "payload": TOOL}, headers=su)).json()
    await client.delete(f"/v1/products/billing/entities/{e['id']}", headers=su)
    assert (await client.get("/v1/products/billing/entities", headers=su)).json() == []
    key = (await client.post("/v1/products/billing/api-keys", headers=su)).json()["plaintext"]
    m = (await client.get("/v1/products/billing/manifest", headers={"X-API-Key": key})).json()
    assert m["entities"] == []
