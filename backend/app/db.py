"""Async SQLAlchemy engine/session. SQLite by default, Postgres via DATABASE_URL."""
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
    _engine = create_async_engine(url or get_settings().database_url, echo=False,
                                  pool_size=10, max_overflow=20, pool_timeout=10)
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
