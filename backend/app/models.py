"""ORM models. Generic registry: entities carry a type (tool | agent) + JSON payload."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def uid() -> str:
    return uuid.uuid4().hex


def now() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255), default="")
    password_hash: Mapped[str] = mapped_column(Text)
    is_super_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    memberships: Mapped[list["Membership"]] = relationship(back_populates="user", cascade="all,delete")


class Product(Base):
    __tablename__ = "products"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    key: Mapped[str] = mapped_column(String(100), unique=True, index=True)   # slug, e.g. "billing"
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default="")
    seq: Mapped[int] = mapped_column(Integer, default=0)                      # monotonic event sequence
    channel_config_enc: Mapped[str] = mapped_column(Text, default="")         # encrypted redis config JSON
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    memberships: Mapped[list["Membership"]] = relationship(back_populates="product", cascade="all,delete")
    audiences: Mapped[list["Audience"]] = relationship(cascade="all,delete")
    entities: Mapped[list["Entity"]] = relationship(back_populates="product", cascade="all,delete")


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("user_id", "product_id"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"), index=True)
    role: Mapped[str] = mapped_column(String(20))  # admin | user
    user: Mapped[User] = relationship(back_populates="memberships")
    product: Mapped[Product] = relationship(back_populates="memberships")


class Audience(Base):
    __tablename__ = "audiences"
    __table_args__ = (UniqueConstraint("product_id", "key"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"), index=True)
    key: Mapped[str] = mapped_column(String(50))          # external | internal | intermediate | ...
    display_name: Mapped[str] = mapped_column(String(100), default="")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)  # the unauthenticated fallback


class ApiKey(Base):
    __tablename__ = "api_keys"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"), index=True)
    name: Mapped[str] = mapped_column(String(100), default="default")
    key_hash: Mapped[str] = mapped_column(String(64), index=True)   # sha256 hex (auth check)
    secret_enc: Mapped[str] = mapped_column(Text, default="")       # encrypted plaintext (copy/reveal)
    prefix: Mapped[str] = mapped_column(String(12))                 # display hint
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Entity(Base):
    __tablename__ = "entities"
    __table_args__ = (UniqueConstraint("product_id", "type", "name"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"), index=True)
    type: Mapped[str] = mapped_column(String(20), default="tool")   # tool | agent
    name: Mapped[str] = mapped_column(String(200), index=True)
    payload: Mapped[dict] = mapped_column(JSON)                     # base + audience overlays
    resolved: Mapped[dict] = mapped_column(JSON, default=dict)      # audience -> resolved view (write-time)
    version: Mapped[int] = mapped_column(Integer, default=1)
    embedding: Mapped[list] = mapped_column(JSON, default=list)     # vector (JSON for sqlite; pgvector in PG)
    embedding_model: Mapped[str] = mapped_column(String(100), default="")
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    product: Mapped[Product] = relationship(back_populates="entities")
    versions: Mapped[list["EntityVersion"]] = relationship(back_populates="entity", cascade="all,delete")


class EntityVersion(Base):
    __tablename__ = "entity_versions"
    __table_args__ = (UniqueConstraint("entity_id", "version"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    entity_id: Mapped[str] = mapped_column(ForeignKey("entities.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict] = mapped_column(JSON)
    author_id: Mapped[str] = mapped_column(String(32), default="")
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    entity: Mapped[Entity] = relationship(back_populates="versions")


class ProductSettings(Base):
    """Per-product tunables editable by product admins (e.g. similarity threshold)."""
    __tablename__ = "product_settings"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"), unique=True, index=True)
    data: Mapped[dict] = mapped_column(JSON, default=dict)


class AiConfig(Base):
    """Reserved: per-product AI gateway config (feature currently removed)."""
    __tablename__ = "ai_configs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"), unique=True, index=True)
    config_enc: Mapped[str] = mapped_column(Text, default="")


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    actor_id: Mapped[str] = mapped_column(String(32), index=True)
    actor_email: Mapped[str] = mapped_column(String(255), default="")
    product_id: Mapped[str] = mapped_column(String(32), default="", index=True)
    action: Mapped[str] = mapped_column(String(60))     # entity.update, product.create, ...
    target: Mapped[str] = mapped_column(String(255), default="")
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
