"""App factory + bootstrap. `uvicorn app.main:app` to run."""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from .api import ai, audit, auth, entities, manifest, products
from .config import get_settings
from .db import Base, engine, session_factory
from .models import User
from .security import hash_password


async def bootstrap():
    """First super admin + dev-convenience schema.

    SQLite (local dev): create_all keeps zero-friction startup.
    Postgres (prod): schema is Alembic-managed — the Docker image runs
    `alembic upgrade head` before uvicorn; create_all is skipped so the
    migration history stays the single source of truth."""
    if engine().url.get_backend_name() == "sqlite":
        async with engine().begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    st = get_settings()
    async with session_factory()() as db:
        existing = (await db.execute(select(User).where(User.is_super_admin == True))).scalars().first()  # noqa: E712
        if not existing:
            db.add(User(email=st.bootstrap_admin_email, name="Bootstrap Admin",
                        password_hash=hash_password(st.bootstrap_admin_password),
                        is_super_admin=True))
            await db.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await bootstrap()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title=get_settings().app_name, version="1.0.0", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
                       allow_headers=["*"])
    for r in (auth.router, products.router, entities.router, manifest.router, audit.router, ai.router):
        app.include_router(r)

    @app.get("/healthz")
    async def health():
        return {"ok": True}

    return app


app = create_app()
