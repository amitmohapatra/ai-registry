"""Manifest caching + sequence numbers + pub/sub events + similarity endpoints."""
import asyncio
import copy

from .conftest import TOOL, login, make_product


async def _key(client, su, product="billing"):
    return (await client.post(f"/v1/products/{product}/api-keys", headers=su)).json()["plaintext"]


async def test_manifest_shape_seq_and_etag(client):
    su = await login(client)
    await make_product(client, su, "billing")
    key = await _key(client, su)
    await client.post("/v1/products/billing/entities",
                      json={"type": "tool", "payload": TOOL}, headers=su)
    r = await client.get("/v1/products/billing/manifest", headers={"X-API-Key": key})
    m = r.json()
    assert m["contract"] == "v1" and m["product_key"] == "billing" and m["seq"] == 1
    assert r.headers["etag"] == 'W/"1"'
    assert m["default_audience"] == "external"
    assert m["entities"][0]["name"] == "get_invoice"
    assert m["entities"][0]["views"]["external"]["enabled"]
    assert m["channel"]["name"] == "registry:billing"


async def test_manifest_cache_invalidated_on_write(client):
    su = await login(client)
    await make_product(client, su, "billing")
    key = await _key(client, su)
    m1 = (await client.get("/v1/products/billing/manifest", headers={"X-API-Key": key})).json()
    assert m1["seq"] == 0 and m1["entities"] == []
    # cached read
    from app.cache import cache
    assert await cache.get("manifest:billing") is not None
    # write -> invalidate -> next read reflects the change
    await client.post("/v1/products/billing/entities",
                      json={"type": "tool", "payload": TOOL}, headers=su)
    m2 = (await client.get("/v1/products/billing/manifest", headers={"X-API-Key": key})).json()
    assert m2["seq"] == 1 and len(m2["entities"]) == 1


async def test_events_published_with_monotonic_seq(client):
    su = await login(client)
    await make_product(client, su, "billing")
    from app.events import bus_router
    received = []

    async def listen():
        async for msg in bus_router.default.subscribe("registry:billing"):
            received.append(msg)
            if len(received) >= 3:
                return

    task = asyncio.create_task(listen())
    await asyncio.sleep(0)
    e = (await client.post("/v1/products/billing/entities",
                           json={"type": "tool", "payload": TOOL}, headers=su)).json()
    v2 = copy.deepcopy(TOOL); v2["description"] = "changed"
    await client.put(f"/v1/products/billing/entities/{e['id']}",
                     json={"payload": v2}, headers=su)
    await client.delete(f"/v1/products/billing/entities/{e['id']}", headers=su)
    await asyncio.wait_for(task, timeout=5)
    assert [m["type"] for m in received] == ["entity.created", "entity.updated", "entity.deleted"]
    assert [m["seq"] for m in received] == [1, 2, 3]
    assert received[1]["entity"]["views"]["external"]["spec"]["description"] == "changed"


async def test_similarity_within_and_across_products(client):
    su = await login(client)
    await make_product(client, su, "billing")
    await make_product(client, su, "shipping")
    mk = lambda name, desc: {"name": name, "description": desc,
                             "input_schema": {"type": "object", "properties": {}}}
    e1 = (await client.post("/v1/products/billing/entities", headers=su,
          json={"type": "tool", "payload": mk("get_invoice", "Fetch an invoice by customer id")})).json()
    await client.post("/v1/products/billing/entities", headers=su,
          json={"type": "tool", "payload": mk("fetch_invoice", "Fetch an invoice by customer id")})
    await client.post("/v1/products/billing/entities", headers=su,
          json={"type": "tool", "payload": mk("send_email", "Send a marketing email blast")})
    await client.post("/v1/products/shipping/entities", headers=su,
          json={"type": "tool", "payload": mk("get_invoice_copy", "Fetch an invoice by customer id")})
    # within product: the twin ranks first, the unrelated tool ranks last
    sim = (await client.get(f"/v1/products/billing/entities/{e1['id']}/similar",
                            params={"scope": "product"}, headers=su)).json()
    assert sim[0]["name"] == "fetch_invoice" and sim[0]["score"] > 0.8
    assert sim[-1]["name"] == "send_email" and sim[-1]["score"] < 0.5
    # across products: shipping twin appears
    sim_all = (await client.get(f"/v1/products/billing/entities/{e1['id']}/similar",
                                params={"scope": "all"}, headers=su)).json()
    assert {s["product_key"] for s in sim_all} == {"billing", "shipping"}


async def test_duplicates_report(client):
    su = await login(client)
    await make_product(client, su, "billing")
    await make_product(client, su, "shipping")
    mk = lambda name, desc: {"name": name, "description": desc,
                             "input_schema": {"type": "object", "properties": {}}}
    await client.post("/v1/products/billing/entities", headers=su,
        json={"type": "tool", "payload": mk("get_invoice", "Fetch an invoice by customer id")})
    await client.post("/v1/products/shipping/entities", headers=su,
        json={"type": "tool", "payload": mk("fetch_invoice", "Fetch an invoice by customer id")})
    rep = (await client.get("/v1/products/billing/entities/reports/duplicates",
                            params={"threshold": 0.8}, headers=su)).json()
    assert len(rep["pairs"]) == 1
    pair = rep["pairs"][0]
    assert pair["cross_product"] is True and pair["score"] >= 0.8


async def test_overridden_description_affects_embedding(client):
    su = await login(client)
    await make_product(client, su, "billing")
    await client.post("/v1/products/billing/audiences", json={"key": "internal"}, headers=su)
    payload = {"name": "tool_a", "description": "totally unrelated words here",
               "input_schema": {"type": "object", "properties": {}},
               "audiences": {"internal": {"overrides": {
                   "description": "Fetch an invoice by customer id"}}}}
    a = (await client.post("/v1/products/billing/entities", headers=su,
                           json={"type": "tool", "payload": payload})).json()
    await client.post("/v1/products/billing/entities", headers=su,
        json={"type": "tool", "payload": {"name": "tool_b",
              "description": "Fetch an invoice by customer id",
              "input_schema": {"type": "object", "properties": {}}}})
    sim = (await client.get(f"/v1/products/billing/entities/{a['id']}/similar",
                            headers=su)).json()
    assert sim[0]["name"] == "tool_b" and sim[0]["score"] > 0.3


async def test_duplicate_scores_are_valid_percentages(client):
    """Scores are cosine in [0,1] (UI renders as %); identical text scores ~1.0,
    and pairs come back sorted best-first."""
    su = await login(client)
    await make_product(client, su, "p1")
    await make_product(client, su, "p2")
    mk = lambda name, desc: {"name": name, "description": desc,
                             "input_schema": {"type": "object", "properties": {}}}
    # identical name+description across products -> perfect duplicate
    await client.post("/v1/products/p1/entities", headers=su,
        json={"type": "tool", "payload": mk("sync_users", "Synchronise user accounts nightly.")})
    await client.post("/v1/products/p2/entities", headers=su,
        json={"type": "tool", "payload": mk("sync_users", "Synchronise user accounts nightly.")})
    await client.post("/v1/products/p2/entities", headers=su,
        json={"type": "tool", "payload": mk("sync_user_accounts", "Synchronise user accounts each night.")})
    rep = (await client.get("/v1/products/p1/entities/reports/duplicates",
                            params={"threshold": 0.4}, headers=su)).json()
    pairs = rep["pairs"]
    assert pairs, "expected duplicate pairs"
    assert all(0.0 <= p["score"] <= 1.0 for p in pairs)
    assert pairs == sorted(pairs, key=lambda p: -p["score"])          # best first
    top = pairs[0]
    assert top["score"] > 0.999 and top["cross_product"] is True      # identical -> 100%
    names = {top["a"]["name"], top["b"]["name"]}
    assert names == {"sync_users"}


async def test_manifest_conditional_304(client):
    su = await login(client)
    await make_product(client, su, "billing")
    key = await _key(client, su)
    r = await client.get("/v1/products/billing/manifest", headers={"X-API-Key": key})
    etag = r.headers["etag"]
    # unchanged -> free 304, no body
    r = await client.get("/v1/products/billing/manifest",
                         headers={"X-API-Key": key, "If-None-Match": etag})
    assert r.status_code == 304 and not r.content
    # a write invalidates the etag
    await client.post("/v1/products/billing/entities",
                      json={"type": "tool", "payload": TOOL}, headers=su)
    r = await client.get("/v1/products/billing/manifest",
                         headers={"X-API-Key": key, "If-None-Match": etag})
    assert r.status_code == 200 and r.json()["seq"] == 1
