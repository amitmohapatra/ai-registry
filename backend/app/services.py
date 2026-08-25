"""Domain services: the write pipeline (validate -> resolve -> commit -> publish),
manifest assembly + caching, sequence numbering, audit."""
import asyncio
from typing import List, Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from . import overlay as ov
from .cache import cache
from .config import get_settings
from .crypto import decrypt_json, encrypt_json
from .embeddings import build_provider
from .events import bus_router
from .models import Audience, AuditLog, Entity, EntityVersion, Product
from .similarity import embed_text_of

_embedder = None
_embed_lock = asyncio.Lock()


def embedder():
    global _embedder
    if _embedder is None:
        st = get_settings()
        _embedder = build_provider(st.embedding_provider, st.embedding_api_key, st.embedding_model)
    return _embedder


def manifest_cache_key(product_key: str) -> str:
    return f"manifest:{product_key}"


async def audience_keys(db: AsyncSession, product_id: str) -> List[str]:
    rows = (await db.execute(select(Audience.key).where(Audience.product_id == product_id))).scalars().all()
    return list(rows) or ["external"]


async def default_audience(db: AsyncSession, product_id: str) -> str:
    row = (await db.execute(select(Audience.key).where(
        Audience.product_id == product_id, Audience.is_default == True))).scalars().first()  # noqa: E712
    return row or "external"


async def next_seq(db: AsyncSession, product: Product) -> int:
    """Monotonic per-product sequence, bumped atomically inside the caller's transaction."""
    await db.execute(update(Product).where(Product.id == product.id).values(seq=Product.seq + 1))
    await db.refresh(product)
    return product.seq


async def build_manifest(db: AsyncSession, product: Product) -> dict:
    auds = await audience_keys(db, product.id)
    default = await default_audience(db, product.id)
    entities = (await db.execute(select(Entity).where(
        Entity.product_id == product.id, Entity.is_deleted == False))).scalars().all()  # noqa: E712
    channel = decrypt_json(product.channel_config_enc)
    return {
        "contract": "v1",
        "product_key": product.key,
        "seq": product.seq,
        "audiences": auds,
        "default_audience": default,
        "channel": {
            "redis_url": channel.get("redis_url", ""),
            "name": f"{channel.get('channel_prefix', 'registry')}:{product.key}",
        },
        "entities": [
            {"id": e.id, "type": e.type, "name": e.name, "version": e.version, "views": e.resolved}
            for e in entities
        ],
    }


async def cached_manifest(db: AsyncSession, product: Product) -> dict:
    key = manifest_cache_key(product.key)
    hit = await cache.get(key)
    if hit is not None and hit.get("seq") == product.seq:
        return hit
    manifest = await build_manifest(db, product)
    await cache.set(key, manifest, get_settings().manifest_cache_ttl)
    return manifest


async def publish_change(db: AsyncSession, product: Product, event_type: str, body: dict) -> None:
    """Invalidate cache + notify the product's channel. Same signal for both."""
    await cache.delete(manifest_cache_key(product.key))
    channel = decrypt_json(product.channel_config_enc)
    name = f"{channel.get('channel_prefix', 'registry')}:{product.key}"
    message = {"contract": "v1", "seq": product.seq, "product_key": product.key,
               "type": event_type, **body}
    await bus_router.publish(name, message, channel.get("redis_url", ""))


async def reembed(db: AsyncSession, entity: Entity) -> None:
    async with _embed_lock:
        vecs = await embedder().embed([embed_text_of(entity.payload)])
    entity.embedding = vecs[0]
    entity.embedding_model = embedder().name


async def write_entity(db: AsyncSession, product: Product, entity: Optional[Entity],
                       entity_type: str, payload: dict, author_id: str, note: str = "") -> tuple:
    """The atomic pipeline. Returns (entity, errors). Nothing partial ever commits."""
    auds = await audience_keys(db, product.id)
    errors = ov.validate_entity(entity_type, payload, auds)
    if errors:
        return None, errors
    resolved = ov.resolve_all(entity_type, payload, auds)
    if entity is None:
        entity = Entity(product_id=product.id, type=entity_type, name=payload["name"],
                        payload=payload, resolved=resolved, version=1)
        db.add(entity)
        event = "entity.created"
    else:
        entity.payload, entity.resolved = payload, resolved
        entity.name = payload["name"]
        entity.version += 1
        event = "entity.updated"
    await reembed(db, entity)
    await db.flush()
    db.add(EntityVersion(entity_id=entity.id, version=entity.version,
                         payload=payload, author_id=author_id, note=note))
    await next_seq(db, product)
    await db.commit()
    await publish_change(db, product, event, {
        "entity": {"id": entity.id, "type": entity.type, "name": entity.name,
                   "version": entity.version, "views": resolved}})
    return entity, []


async def delete_entity(db: AsyncSession, product: Product, entity: Entity, author_id: str) -> None:
    entity.is_deleted = True
    await next_seq(db, product)
    await db.commit()
    await publish_change(db, product, "entity.deleted", {
        "entity": {"id": entity.id, "type": entity.type, "name": entity.name}})


async def audit(db: AsyncSession, actor, action: str, target: str = "",
                product_id: str = "", detail: Optional[dict] = None) -> None:
    db.add(AuditLog(actor_id=getattr(actor, "id", ""), actor_email=getattr(actor, "email", ""),
                    product_id=product_id, action=action, target=target, detail=detail or {}))
    await db.commit()
