"""Auth, RBAC, product onboarding, membership — the control-plane security model."""
from .conftest import TOOL, login, make_product, make_user


async def test_bootstrap_admin_and_login(client):
    su = await login(client)
    r = await client.get("/v1/auth/me", headers=su)
    assert r.json()["is_super_admin"] is True


async def test_bad_password_rejected(client):
    r = await client.post("/v1/auth/login", json={"email": "admin@registry.dev", "password": "wrong"})
    assert r.status_code == 401


async def test_no_token_rejected(client):
    assert (await client.get("/v1/products")).status_code == 401
    assert (await client.get("/v1/products", headers={"Authorization": "Bearer junk"})).status_code == 401


async def test_only_super_admin_creates_products_and_users(client):
    su = await login(client)
    await make_user(client, su, "dev@co.com")
    dev = await login(client, "dev@co.com", "secret1")
    r = await client.post("/v1/products", json={"key": "x", "name": "X"}, headers=dev)
    assert r.status_code == 403
    r = await client.post("/v1/auth/users", json={"email": "a@b.c", "password": "secret1"}, headers=dev)
    assert r.status_code == 403


async def test_product_scoping_by_membership(client):
    su = await login(client)
    await make_product(client, su, "billing")
    await make_product(client, su, "shipping")
    await make_user(client, su, "alice@co.com")
    await client.put("/v1/products/billing/members",
                     json={"email": "alice@co.com", "role": "admin"}, headers=su)
    alice = await login(client, "alice@co.com", "secret1")
    products = (await client.get("/v1/products", headers=alice)).json()
    assert [p["key"] for p in products] == ["billing"]
    assert products[0]["role"] == "admin"
    # no access to shipping at all
    assert (await client.get("/v1/products/shipping", headers=alice)).status_code == 403
    # super admin sees everything
    assert len((await client.get("/v1/products", headers=su)).json()) == 2


async def test_member_role_user_is_read_only(client):
    su = await login(client)
    await make_product(client, su, "billing")
    await make_user(client, su, "bob@co.com")
    await client.put("/v1/products/billing/members",
                     json={"email": "bob@co.com", "role": "user"}, headers=su)
    bob = await login(client, "bob@co.com", "secret1")
    # can read
    assert (await client.get("/v1/products/billing/entities", headers=bob)).status_code == 200
    # cannot write
    r = await client.post("/v1/products/billing/entities",
                          json={"type": "tool", "payload": TOOL}, headers=bob)
    assert r.status_code == 403


async def test_admin_cannot_touch_other_product(client):
    su = await login(client)
    await make_product(client, su, "billing")
    await make_product(client, su, "shipping")
    await make_user(client, su, "alice@co.com")
    await client.put("/v1/products/billing/members",
                     json={"email": "alice@co.com", "role": "admin"}, headers=su)
    alice = await login(client, "alice@co.com", "secret1")
    r = await client.post("/v1/products/shipping/entities",
                          json={"type": "tool", "payload": TOOL}, headers=alice)
    assert r.status_code == 403


async def test_channel_config_super_admin_only_and_encrypted(client):
    su = await login(client)
    await make_product(client, su, "billing")
    r = await client.put("/v1/products/billing/channel",
                         json={"redis_url": "redis://prod:6379/0"}, headers=su)
    assert r.status_code == 204
    got = (await client.get("/v1/products/billing/channel", headers=su)).json()
    assert got["redis_url"] == "redis://prod:6379/0"
    # stored encrypted at rest
    from app.db import session_factory
    from app.models import Product
    from sqlalchemy import select
    async with session_factory()() as db:
        p = (await db.execute(select(Product).where(Product.key == "billing"))).scalars().first()
        assert "redis://" not in p.channel_config_enc
    # product admin cannot read it
    await make_user(client, su, "alice@co.com")
    await client.put("/v1/products/billing/members",
                     json={"email": "alice@co.com", "role": "admin"}, headers=su)
    alice = await login(client, "alice@co.com", "secret1")
    assert (await client.get("/v1/products/billing/channel", headers=alice)).status_code == 403


async def test_api_key_lifecycle(client):
    su = await login(client)
    await make_product(client, su, "billing")
    created = (await client.post("/v1/products/billing/api-keys", headers=su)).json()
    assert created["plaintext"].startswith("trk_")
    # key works on the manifest endpoint
    r = await client.get("/v1/products/billing/manifest",
                         headers={"X-API-Key": created["plaintext"]})
    assert r.status_code == 200
    # revoke -> stops working
    await client.delete(f"/v1/products/billing/api-keys/{created['id']}", headers=su)
    r = await client.get("/v1/products/billing/manifest",
                         headers={"X-API-Key": created["plaintext"]})
    assert r.status_code == 401
    # missing/garbage keys rejected
    assert (await client.get("/v1/products/billing/manifest")).status_code == 401
    assert (await client.get("/v1/products/billing/manifest",
                             headers={"X-API-Key": "nope"})).status_code == 401


async def test_audit_log_records_actions(client):
    su = await login(client)
    await make_product(client, su, "billing")
    await client.post("/v1/products/billing/entities",
                      json={"type": "tool", "payload": TOOL}, headers=su)
    rows = (await client.get("/v1/products/billing/audit", headers=su)).json()
    actions = [r["action"] for r in rows]
    assert "entity.create" in actions and "product.create" in actions


async def test_entity_pagination_and_search(client):
    su = await login(client)
    await make_product(client, su, "big")
    for i in range(12):
        await client.post("/v1/products/big/entities", headers=su, json={"type": "tool",
            "payload": {"name": f"tool_{i:02d}", "description": f"Tool number {i}.",
                        "input_schema": {"type": "object", "properties": {}}}})
    r = await client.get("/v1/products/big/entities", params={"limit": 5}, headers=su)
    assert len(r.json()) == 5 and r.headers["x-total-count"] == "12"
    r2 = await client.get("/v1/products/big/entities", params={"limit": 5, "offset": 10}, headers=su)
    assert [e["name"] for e in r2.json()] == ["tool_10", "tool_11"]
    # search narrows results and total
    r3 = await client.get("/v1/products/big/entities", params={"q": "tool_0"}, headers=su)
    assert len(r3.json()) == 10 and r3.headers["x-total-count"] == "10"
    r4 = await client.get("/v1/products/big/entities", params={"q": "tool_07"}, headers=su)
    assert [e["name"] for e in r4.json()] == ["tool_07"]


async def test_excel_export_rbac(client):
    su = await login(client)
    await make_product(client, su, "billing")
    await client.post("/v1/products/billing/entities",
                      json={"type": "tool", "payload": TOOL}, headers=su)
    # product export: any member
    r = await client.get("/v1/products/billing/entities/reports/export", headers=su)
    assert r.status_code == 200 and r.content[:2] == b"PK"       # xlsx = zip magic
    assert "tools-billing.xlsx" in r.headers["content-disposition"]
    # all-products export: super admin only
    r = await client.get("/v1/products/billing/entities/reports/export",
                         params={"scope": "all"}, headers=su)
    assert r.status_code == 200 and "all-products" in r.headers["content-disposition"]
    await make_user(client, su, "alice@co.com")
    await client.put("/v1/products/billing/members",
                     json={"email": "alice@co.com", "role": "user"}, headers=su)
    alice = await login(client, "alice@co.com", "secret1")
    assert (await client.get("/v1/products/billing/entities/reports/export",
                             headers=alice)).status_code == 200          # own product: ok
    assert (await client.get("/v1/products/billing/entities/reports/export",
                             params={"scope": "all"}, headers=alice)).status_code == 403


async def test_single_active_api_key_rotation(client):
    su = await login(client)
    await make_product(client, su, "billing")
    k1 = (await client.post("/v1/products/billing/api-keys", headers=su)).json()
    assert (await client.get("/v1/products/billing/manifest",
                             headers={"X-API-Key": k1["plaintext"]})).status_code == 200
    # creating a second key ROTATES: k1 dies in the same moment k2 is born
    k2 = (await client.post("/v1/products/billing/api-keys", headers=su)).json()
    assert (await client.get("/v1/products/billing/manifest",
                             headers={"X-API-Key": k1["plaintext"]})).status_code == 401
    assert (await client.get("/v1/products/billing/manifest",
                             headers={"X-API-Key": k2["plaintext"]})).status_code == 200
    # exactly one active key, ever
    keys = (await client.get("/v1/products/billing/api-keys", headers=su)).json()
    assert sum(1 for k in keys if not k["revoked"]) == 1


async def test_product_hard_delete_frees_key_and_cleans_up(client):
    su = await login(client)
    await make_product(client, su, "billing")
    await client.post("/v1/products/billing/entities",
                      json={"type": "tool", "payload": TOOL}, headers=su)
    key = (await client.post("/v1/products/billing/api-keys", headers=su)).json()["plaintext"]
    # duplicate key is rejected while the product exists
    r = await client.post("/v1/products", json={"key": "billing", "name": "Again"}, headers=su)
    assert r.status_code == 409
    # super admin deletes -> product gone, its API key dead
    assert (await client.delete("/v1/products/billing", headers=su)).status_code == 204
    assert (await client.get("/v1/products/billing", headers=su)).status_code == 404
    assert (await client.get("/v1/products/billing/manifest",
                             headers={"X-API-Key": key})).status_code == 404
    # the key name is FREE again — a fresh product can use it, starting empty
    r = await client.post("/v1/products", json={"key": "billing", "name": "Reborn"}, headers=su)
    assert r.status_code == 201
    assert (await client.get("/v1/products/billing/entities", headers=su)).json() == []
    # non-super-admin cannot delete
    await make_user(client, su, "alice@co.com")
    await client.put("/v1/products/billing/members",
                     json={"email": "alice@co.com", "role": "admin"}, headers=su)
    alice = await login(client, "alice@co.com", "secret1")
    assert (await client.delete("/v1/products/billing", headers=alice)).status_code == 403


async def test_api_key_reveal_for_copy(client):
    su = await login(client)
    await make_product(client, su, "billing")
    created = (await client.post("/v1/products/billing/api-keys", headers=su)).json()
    revealed = (await client.get("/v1/products/billing/api-keys/reveal", headers=su)).json()
    assert revealed["plaintext"] == created["plaintext"]          # copy works any time
    # the revealed key actually authenticates
    r = await client.get("/v1/products/billing/manifest",
                         headers={"X-API-Key": revealed["plaintext"]})
    assert r.status_code == 200
    # read-only members cannot reveal
    await make_user(client, su, "bob@co.com")
    await client.put("/v1/products/billing/members",
                     json={"email": "bob@co.com", "role": "user"}, headers=su)
    bob = await login(client, "bob@co.com", "secret1")
    assert (await client.get("/v1/products/billing/api-keys/reveal",
                             headers=bob)).status_code == 403
