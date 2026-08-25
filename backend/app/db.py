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
    _engine = create_async_engine(url or get_settings().database_url, echo=False)
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
