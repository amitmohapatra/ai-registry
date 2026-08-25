"""All 6 JSON Schema types end-to-end, audience deletion semantics, and
similarity quality on paraphrases (the 'top notch' bar for a local embedder)."""
import copy

from .conftest import login, make_product

ALL_TYPES = {
    "name": "kitchen_sink",
    "description": "Exercises every supported parameter type.",
    "input_schema": {
        "type": "object",
        "properties": {
            "s": {"type": "string", "description": "a string", "minLength": 1},
            "i": {"type": "integer", "description": "an integer", "minimum": 0},
            "n": {"type": "number", "description": "a float", "exclusiveMinimum": 0},
            "b": {"type": "boolean", "description": "a flag", "default": False},
            "arr": {"type": "array", "description": "an array of objects",
                    "items": {"type": "object",
                              "properties": {"k": {"type": "string", "description": "key"}},
                              "required": ["k"]}},
            "obj": {"type": "object", "description": "a nested object",
                    "properties": {"inner": {"type": "array", "description": "inner array",
                                             "items": {"type": "number"}}}},
        },
        "required": ["s", "i"],
    },
}


async def test_all_six_types_roundtrip_with_descriptions(client):
    su = await login(client)
    await make_product(client, su, "types")
    r = await client.post("/v1/products/types/entities",
                          json={"type": "tool", "payload": ALL_TYPES}, headers=su)
    assert r.status_code == 201, r.text
    props = r.json()["resolved"]["external"]["spec"]["input_schema"]["properties"]
    assert {p: props[p]["type"] for p in props} == {
        "s": "string", "i": "integer", "n": "number", "b": "boolean",
        "arr": "array", "obj": "object"}
    for p in props:                                   # every description preserved
        assert props[p]["description"], p
    assert props["arr"]["items"]["properties"]["k"]["description"] == "key"
    assert props["obj"]["properties"]["inner"]["items"]["type"] == "number"


async def test_all_types_pinnable_and_modifiable(client):
    su = await login(client)
    await make_product(client, su, "types")
    await client.post("/v1/products/types/audiences", json={"key": "internal"}, headers=su)
    payload = copy.deepcopy(ALL_TYPES)
    payload["audiences"] = {"external": {"overrides": {"parameters": {"hide": {
        "b": {"pin": True},
        "n": {"pin": 1.5},
        "arr": {"pin": [{"k": "fixed"}]},
        "obj": {"pin": {"inner": [1, 2.5]}},
    }, "modify": {"s": {"description": "external-facing string"}}}}}}
    r = await client.post("/v1/products/types/entities",
                          json={"type": "tool", "payload": payload}, headers=su)
    assert r.status_code == 201, r.text
    ext = r.json()["resolved"]["external"]
    assert ext["pins"] == {"b": True, "n": 1.5, "arr": [{"k": "fixed"}],
                           "obj": {"inner": [1, 2.5]}}
    assert ext["spec"]["input_schema"]["properties"]["s"]["description"] == "external-facing string"
    # array pin violating item schema is rejected
    bad = copy.deepcopy(payload)
    bad["audiences"]["external"]["overrides"]["parameters"]["hide"]["arr"]["pin"] = [{"nope": 1}]
    bad["name"] = "kitchen_sink2"
    r = await client.post("/v1/products/types/entities",
                          json={"type": "tool", "payload": bad}, headers=su)
    assert r.status_code == 422
    assert any(e["code"] == "invalid_pin" and "arr" in e["path"]
               for e in r.json()["detail"]["errors"])

# ---- audience deletion ----

async def test_delete_audience_strips_overlays_and_republishes(client):
    su = await login(client)
    await make_product(client, su, "billing")
    await client.post("/v1/products/billing/audiences", json={"key": "internal"}, headers=su)
    payload = copy.deepcopy(ALL_TYPES)
    payload["audiences"] = {"internal": {"overrides": {"description": "internal view"}}}
    e = (await client.post("/v1/products/billing/entities",
                           json={"type": "tool", "payload": payload}, headers=su)).json()
    assert e["version"] == 1 and "internal" in e["resolved"]
    r = await client.delete("/v1/products/billing/audiences/internal", headers=su)
    assert r.status_code == 204
    got = (await client.get(f"/v1/products/billing/entities/{e['id']}", headers=su)).json()
    assert got["version"] == 2                                  # strip = auditable new version
    assert "internal" not in got["payload"].get("audiences", {})
    assert set(got["resolved"]) == {"external"}
    auds = (await client.get("/v1/products/billing/audiences", headers=su)).json()
    assert [a["key"] for a in auds] == ["external"]
    # manifest no longer carries the audience
    key = (await client.post("/v1/products/billing/api-keys", headers=su)).json()["plaintext"]
    m = (await client.get("/v1/products/billing/manifest", headers={"X-API-Key": key})).json()
    assert m["audiences"] == ["external"]
    assert set(m["entities"][0]["views"]) == {"external"}


async def test_cannot_delete_default_audience(client):
    su = await login(client)
    await make_product(client, su, "billing")
    r = await client.delete("/v1/products/billing/audiences/external", headers=su)
    assert r.status_code == 400


async def test_delete_missing_audience_404(client):
    su = await login(client)
    await make_product(client, su, "billing")
    assert (await client.delete("/v1/products/billing/audiences/ghost",
                                headers=su)).status_code == 404

# ---- similarity quality: paraphrases must now score high ----

async def test_paraphrase_duplicates_score_high(client):
    su = await login(client)
    await make_product(client, su, "p1")
    await make_product(client, su, "p2")
    mk = lambda name, desc: {"name": name, "description": desc,
                             "input_schema": {"type": "object", "properties": {}}}
    pairs = [
        ("get_invoice", "Fetch an invoice by its ID.",
         "fetch_invoice", "Fetch an invoice by ID for a customer order."),
        ("send_notification", "Send a push notification to a user device.",
         "notify_user", "Sends push notifications to users' devices."),
    ]
    ids = []
    for a_name, a_desc, b_name, b_desc in pairs:
        ra = await client.post("/v1/products/p1/entities", headers=su,
                               json={"type": "tool", "payload": mk(a_name, a_desc)})
        await client.post("/v1/products/p2/entities", headers=su,
                          json={"type": "tool", "payload": mk(b_name, b_desc)})
        ids.append(ra.json()["id"])
    # unrelated distractor
    await client.post("/v1/products/p2/entities", headers=su,
        json={"type": "tool", "payload": mk("rotate_logs", "Rotate and compress server log files weekly.")})
    for eid in ids:
        sim = (await client.get(f"/v1/products/p1/entities/{eid}/similar",
                                params={"scope": "all"}, headers=su)).json()
        assert sim[0]["score"] >= 0.6, sim                     # paraphrase twin on top
        assert sim[-1]["score"] <= 0.35                        # distractor clearly below
    rep = (await client.get("/v1/products/p1/entities/reports/duplicates",
                            params={"threshold": 0.6, "scope": "all"}, headers=su)).json()
    found = {frozenset((p["a"]["name"], p["b"]["name"])) for p in rep["pairs"]}
    assert {frozenset(("get_invoice", "fetch_invoice")),
            frozenset(("send_notification", "notify_user"))} <= found

# ---- equivalence-score unit tests (stub reranker: CI never downloads models) ----

class _StubReranker:
    """Pretends to be a relevance cross-encoder that loves topical overlap."""
    def score_pairs(self, query, candidates):
        return [5.0 for _ in candidates]      # sigmoid(5) ~ 0.99 for everything


def test_equivalence_guards():
    from app.similarity import action_class, equivalence_score
    rr = _StubReranker()
    # thin description can never claim high similarity
    s = equivalence_score(rr, "fetch", "Fetch an invoice by its ID.")
    assert s is not None and s <= 0.35
    # different action classes are capped even when the model says 99%
    s = equivalence_score(rr, "Create a new invoice for a customer order.",
                          "Fetch an invoice by ID for a customer order.")
    assert s == 0.45
    # same action class passes through untouched
    s = equivalence_score(rr, "Fetch an invoice by its ID.",
                          "Retrieve a billing document using its identifier.")
    assert s > 0.9
    # verb classification incl. inflections
    assert action_class("Fetches the latest invoice") == "read"
    assert action_class("Creating new user accounts") == "create"
    assert action_class("Permanently deletes records") == "delete"
    assert action_class("Weekly rotation of logs") is None
