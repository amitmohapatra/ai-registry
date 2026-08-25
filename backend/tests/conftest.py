import asyncio
import os
import sys

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("REGISTRY_JWT_SECRET", "test-secret")
os.environ.setdefault("REGISTRY_EMBEDDING_PROVIDER", "hashing")   # deterministic, no downloads
os.environ.setdefault("REGISTRY_RERANKER", "none")


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture()
async def app(tmp_path):
    """Fresh app + fresh sqlite DB per test."""
    from app import db as dbmod
    from app import services, cache as cachemod
    dbmod.init_engine(f"sqlite+aiosqlite:///{tmp_path}/test.db")
    cachemod.cache.l1._store.clear()
    services._embedder = None
    import app.similarity as simmod
    simmod._reranker = None
    from app.main import create_app, bootstrap
    application = create_app()
    await bootstrap()
    return application


@pytest_asyncio.fixture()
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def login(client, email="admin@registry.dev", password="admin") -> dict:
    r = await client.post("/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def make_product(client, su, key="billing"):
    r = await client.post("/v1/products", json={"key": key, "name": key.title()}, headers=su)
    assert r.status_code == 201, r.text
    return r.json()


async def make_user(client, su, email, password="secret1", **kw):
    r = await client.post("/v1/auth/users", json={"email": email, "password": password, **kw}, headers=su)
    assert r.status_code == 201, r.text
    return r.json()


TOOL = {
    "name": "get_invoice",
    "description": "Fetch an invoice by its ID.",
    "input_schema": {
        "type": "object",
        "properties": {
            "invoice_id": {"type": "string", "description": "Invoice identifier"},
            "max_results": {"type": "integer", "minimum": 1, "maximum": 1000},
        },
        "required": ["invoice_id"],
    },
}
