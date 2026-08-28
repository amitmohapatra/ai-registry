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


_FIELD_NAMES = {"key": "Product key", "email": "Email", "password": "Password",
                "name": "Name", "role": "Role", "payload": "Tool definition",
                "similarity_threshold": "Similarity threshold"}

_FRIENDLY = {
    ("password", "string_too_short"): "Password needs at least 6 characters.",
    ("key", "string_pattern_mismatch"): ("Product key must start with a letter and may "
                                         "contain letters, numbers, underscores and hyphens."),
    ("key", "string_too_short"): "Product key needs at least 2 characters.",
    ("email", "value_error"): "Enter a valid email address.",
    ("role", "string_pattern_mismatch"): "Role must be 'admin' or 'user'.",
}


def _friendly_errors(errors) -> list:
    out = []
    for e in errors:
        field = str(e["loc"][-1]) if e.get("loc") else ""
        msg = _FRIENDLY.get((field, e.get("type", "")))
        if not msg:
            label = _FIELD_NAMES.get(field, field.replace("_", " ").capitalize() or "Input")
            msg = f"{label}: {e.get('msg', 'invalid value')}"
        out.append({"loc": e.get("loc", []), "msg": msg, "type": e.get("type", "")})
    return out


def create_app() -> FastAPI:
    app = FastAPI(
        title=get_settings().app_name,
        version="1.0.0",
        lifespan=lifespan,
        description=(
            "Runtime registry for MCP tools and agents: audiences, versioning, RBAC, "
            "semantic duplicate detection, and per-product pub/sub so connected MCP "
            "servers hot-reload metadata in milliseconds. Interactive docs: /docs"
        ),
        openapi_tags=[
            {"name": "auth", "description": "Login, users (super admin)"},
            {"name": "products", "description": "Onboarding, members, audiences, API key, channels"},
            {"name": "entities", "description": "Tools/agents CRUD, versions, similarity, export"},
            {"name": "manifest", "description": "Data plane consumed by the MCP SDK (API-key auth)"},
            {"name": "ai", "description": "Similarity preview, overlap explain, product settings"},
            {"name": "audit", "description": "Per-product audit log"},
        ],
    )
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
                       allow_headers=["*"])
    for r in (auth.router, products.router, entities.router, manifest.router, audit.router, ai.router):
        app.include_router(r)

    from fastapi.exceptions import RequestValidationError
    from fastapi.responses import JSONResponse

    @app.exception_handler(RequestValidationError)
    async def humane_validation_errors(request, exc: RequestValidationError):
        return JSONResponse(status_code=422, content={"detail": _friendly_errors(exc.errors())})

    @app.get("/healthz")
    async def health():
        return {"ok": True}

    return app


app = create_app()
