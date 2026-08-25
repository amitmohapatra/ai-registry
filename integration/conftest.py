"""End-to-end fixtures: a REAL registry app in-process; the SDK talks to it over
an ASGI transport, and receives events straight off the registry's in-memory bus."""
import asyncio
import os
import sys

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "../backend")))   # registry app
# yourco_mcp comes from the installed package (pip install -e ../mcp-sdk)

os.environ.setdefault("REGISTRY_JWT_SECRET", "test-secret")
os.environ.setdefault("REGISTRY_EMBEDDING_PROVIDER", "hashing")   # deterministic, no downloads
os.environ.setdefault("REGISTRY_RERANKER", "none")

TOOL = {
    "name": "get_invoice",
    "description": "Fetch an invoice by its ID.",
    "input_schema": {
        "type": "object",
        "properties": {
            "invoice_id": {"type": "string"},
            "max_results": {"type": "integer", "minimum": 1, "maximum": 1000},
        },
        "required": ["invoice_id"],
    },
    "audiences": {
        "internal": {"overrides": {
            "description": "Fetch invoice incl. ledger refs.",
            "parameters": {"add": {"include_ledger": {"type": "boolean", "default": False}}},
        }},
        "external": {"overrides": {
            "parameters": {"hide": {"max_results": {"pin": 25}}},
        }},
    },
}

SECURE_TOOL = {
    "name": "refund_payment",
    "description": "Refund a payment.",
    "auth": {"required_scopes": ["payments:write"]},
    "input_schema": {"type": "object",
                     "properties": {"payment_id": {"type": "string"},
                                    "amount": {"type": "number"}},
                     "required": ["payment_id", "amount"]},
}


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


class Registry:
    """Helper wrapping the live registry app for test arrangement."""

    def __init__(self, app, client):
        self.app, self.client = app, client
        self.su = None
        self.api_key = None

    async def login(self):
        r = await self.client.post("/v1/auth/login",
                                   json={"email": "admin@registry.dev", "password": "admin"})
        self.su = {"Authorization": f"Bearer {r.json()['access_token']}"}

    async def setup_product(self, key="billing", audiences=("internal",)):
        await self.client.post("/v1/products", json={"key": key, "name": key}, headers=self.su)
        for aud in audiences:
            await self.client.post(f"/v1/products/{key}/audiences",
                                   json={"key": aud}, headers=self.su)
        r = await self.client.post(f"/v1/products/{key}/api-keys", headers=self.su)
        self.api_key = r.json()["plaintext"]

    async def add_tool(self, payload, key="billing", type_="tool"):
        r = await self.client.post(f"/v1/products/{key}/entities",
                                   json={"type": type_, "payload": payload}, headers=self.su)
        assert r.status_code == 201, r.text
        return r.json()

    async def update_tool(self, entity_id, payload, key="billing"):
        r = await self.client.put(f"/v1/products/{key}/entities/{entity_id}",
                                  json={"payload": payload}, headers=self.su)
        assert r.status_code == 200, r.text
        return r.json()


@pytest_asyncio.fixture()
async def registry(tmp_path):
    from app import db as dbmod, services, cache as cachemod
    import app.events as eventsmod
    dbmod.init_engine(f"sqlite+aiosqlite:///{tmp_path}/registry.db")
    cachemod.cache.l1._store.clear()
    eventsmod.bus_router.__init__()                    # fresh bus per test
    services._embedder = None
    import app.similarity as simmod
    simmod._reranker = None
    from app.main import create_app, bootstrap
    application = create_app()
    await bootstrap()
    async with AsyncClient(transport=ASGITransport(app=application),
                           base_url="http://registry") as client:
        reg = Registry(application, client)
        await reg.login()
        yield reg


@pytest_asyncio.fixture()
async def make_server(registry, tmp_path):
    """Factory: a ProductServer wired to the in-process registry + its bus."""
    from yourco_mcp import ProductServer
    from yourco_mcp.client import RegistryClient
    import app.events as eventsmod
    servers = []

    def factory(**kw):
        client = RegistryClient(
            "http://registry", kw.pop("product_key", "billing"),
            kw.pop("api_key", registry.api_key),
            snapshot_path=str(tmp_path / "snap.json"),
            transport=ASGITransport(app=registry.app))

        async def bus_subscribe(manifest):
            channel = manifest["channel"]["name"]
            async for msg in eventsmod.bus_router.default.subscribe(channel):
                yield msg

        client.subscribe = bus_subscribe               # inject in-memory subscriber
        server = ProductServer("http://registry", client.product_key, client.api_key,
                               client=client, **kw)
        servers.append(server)
        return server

    yield factory
    for s in servers:
        await s.stop()


async def rpc(server, method, params=None, headers=None, rid=1):
    return await server.handle_request(
        {"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}},
        headers or {})
