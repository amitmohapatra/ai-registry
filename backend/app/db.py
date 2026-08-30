"""Async SQLAlchemy engine/session. SQLite by default, Postgres via DATABASE_URL."""
from . import config
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from .config import get_settings


class Base(DeclarativeBase):
    pass


_engine = None
_session_factory = None


def init_engine(url: str = ""):
    global _engine, _session_factory
    # similarity scoring can briefly queue many requests; give the pool
    # headroom so short DB reads never starve behind long CPU work
    # (in-memory SQLite uses StaticPool, which rejects sizing arguments)
    resolved = url or get_settings().database_url
    kw = {} if ":memory:" in resolved else dict(pool_size=config.DB_POOL_SIZE,
                                                 max_overflow=config.DB_MAX_OVERFLOW,
                                                pool_timeout=10)
    _engine = create_async_engine(resolved, echo=False, **kw)
    _session_factory = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)
    return _engine


def engine():
    return _engine or init_engine()


def session_factory():
    if _session_factory is None:
        init_engine()
    return _session_factory


async def get_session() -> AsyncSession:
    async with session_factory()() as s:
        yield s
