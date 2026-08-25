from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import overlay as ov
from ..db import get_session
from ..deps import require_member, require_product_admin
from ..models import Entity, EntityVersion
from ..schemas import DryRunOut, EntityIn, EntityOut, EntityPatch, SimilarOut, VersionOut
from ..services import audience_keys, audit, delete_entity, write_entity
from ..similarity import (apply_rerank, duplicate_pairs, embed_text_of,
                          name_similarity, rank, rerank_pairs)
from ..config import get_settings

router = APIRouter(prefix="/v1/products/{product_key}/entities", tags=["entities"])


async def _get_entity(db: AsyncSession, product_id: str, entity_id: str) -> Entity:
    e = await db.get(Entity, entity_id)
    if not e or e.product_id != product_id or e.is_deleted:
        raise HTTPException(404, "Entity not found")
    return e


@router.post("", response_model=EntityOut, status_code=201)
async def create(body: EntityIn, ctx: tuple = Depends(require_product_admin),
                 db: AsyncSession = Depends(get_session)):
    product, actor, _ = ctx
    name = (body.payload or {}).get("name", "")
    dup = (await db.execute(select(Entity).where(
        Entity.product_id == product.id, Entity.type == body.type,
        Entity.name == name, Entity.is_deleted == False))).scalars().first()  # noqa: E712
    if dup:
        raise HTTPException(409, f"{body.type} '{name}' already exists")
    entity, errors = await write_entity(db, product, None, body.type, body.payload, actor.id, body.note)
    if errors:
        raise HTTPException(422, {"errors": errors})
    await audit(db, actor, "entity.create", name, product.id)
    return entity


@router.get("", response_model=list[EntityOut])
async def list_entities(response: Response, ctx: tuple = Depends(require_member),
                        db: AsyncSession = Depends(get_session),
                        type: str = Query(default=""), q: str = Query(default=""),
                        limit: int = Query(default=100, le=500),
                        offset: int = Query(default=0, ge=0)):
    """Paginated + searchable. Total count is returned in X-Total-Count so the
    response stays a plain array (stable contract for existing clients)."""
    from sqlalchemy import func, or_
    product, _, _ = ctx
    stmt = select(Entity).where(Entity.product_id == product.id, Entity.is_deleted == False)  # noqa: E712
    if type:
        stmt = stmt.where(Entity.type == type)
    if q:
        stmt = stmt.where(Entity.name.ilike(f"%{q}%"))
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar()
    response.headers["X-Total-Count"] = str(total)
    return (await db.execute(stmt.order_by(Entity.name).limit(limit).offset(offset))).scalars().all()


@router.get("/{entity_id}", response_model=EntityOut)
async def get_one(entity_id: str, ctx: tuple = Depends(require_member),
                  db: AsyncSession = Depends(get_session)):
    product, _, _ = ctx
    return await _get_entity(db, product.id, entity_id)


@router.put("/{entity_id}", response_model=EntityOut)
async def update(entity_id: str, body: EntityPatch, ctx: tuple = Depends(require_product_admin),
                 db: AsyncSession = Depends(get_session)):
    product, actor, _ = ctx
    entity = await _get_entity(db, product.id, entity_id)
    entity, errors = await write_entity(db, product, entity, entity.type, body.payload, actor.id, body.note)
    if errors:
        raise HTTPException(422, {"errors": errors})
    await audit(db, actor, "entity.update", entity.name, product.id)
    return entity


@router.delete("/{entity_id}", status_code=204)
async def remove(entity_id: str, ctx: tuple = Depends(require_product_admin),
                 db: AsyncSession = Depends(get_session)):
    product, actor, _ = ctx
    entity = await _get_entity(db, product.id, entity_id)
    await delete_entity(db, product, entity, actor.id)
    await audit(db, actor, "entity.delete", entity.name, product.id)


@router.post("/dry-run", response_model=DryRunOut)
async def dry_run(body: EntityIn, ctx: tuple = Depends(require_member),
                  db: AsyncSession = Depends(get_session)):
    """Validate + resolve WITHOUT committing. Powers the UI live preview —
    the preview and the commit path are the same code, so the preview cannot lie."""
    product, _, _ = ctx
    auds = await audience_keys(db, product.id)
    errors = ov.validate_entity(body.type, body.payload, auds)
    if errors:
        return DryRunOut(valid=False, errors=errors)
    return DryRunOut(valid=True, resolved=ov.resolve_all(body.type, body.payload, auds))

# ---- versions & rollback ----

@router.get("/{entity_id}/versions", response_model=list[VersionOut])
async def versions(entity_id: str, ctx: tuple = Depends(require_member),
                   db: AsyncSession = Depends(get_session)):
    product, _, _ = ctx
    await _get_entity(db, product.id, entity_id)
    return (await db.execute(select(EntityVersion).where(EntityVersion.entity_id == entity_id)
                             .order_by(EntityVersion.version.desc()))).scalars().all()


@router.post("/{entity_id}/rollback/{version}", response_model=EntityOut)
async def rollback(entity_id: str, version: int, ctx: tuple = Depends(require_product_admin),
                   db: AsyncSession = Depends(get_session)):
    """Restore an old payload as a NEW version — the undo path is the normal path."""
    product, actor, _ = ctx
    entity = await _get_entity(db, product.id, entity_id)
    old = (await db.execute(select(EntityVersion).where(
        EntityVersion.entity_id == entity_id, EntityVersion.version == version))).scalars().first()
    if not old:
        raise HTTPException(404, f"Version {version} not found")
    entity, errors = await write_entity(db, product, entity, entity.type, old.payload,
                                        actor.id, f"rollback to v{version}")
    if errors:
        raise HTTPException(422, {"errors": errors})
    await audit(db, actor, "entity.rollback", entity.name, product.id, {"to_version": version})
    return entity

# ---- similarity ----

async def _candidates(db: AsyncSession, scope_product_id: str = "") -> list:
    from ..models import Product as P
    from ..services import embedder
    q = select(Entity, P.key).join(P, Entity.product_id == P.id).where(
        Entity.is_deleted == False)  # noqa: E712
    if scope_product_id:
        q = q.where(Entity.product_id == scope_product_id)
    rows = (await db.execute(q)).all()
    # lazy migration: re-embed anything produced by an older/different embedder so
    # vectors are always comparable (mixing models breaks cosine math)
    stale = [e for e, _ in rows if e.embedding_model != embedder().name]
    if stale:
        vecs = await embedder().embed([embed_text_of(e.payload) for e in stale])
        for e, v in zip(stale, vecs):
            e.embedding, e.embedding_model = v, embedder().name
        await db.commit()
    return [{"id": e.id, "product_id": e.product_id, "product_key": pkey,
             "type": e.type, "name": e.name, "text": embed_text_of(e.payload),
             "payload": e.payload, "vec": e.embedding} for e, pkey in rows]


@router.get("/{entity_id}/similar", response_model=list[SimilarOut])
async def similar(entity_id: str, ctx: tuple = Depends(require_member),
                  db: AsyncSession = Depends(get_session),
                  scope: str = Query(default="product", pattern="^(product|all)$"),
                  top_k: int = Query(default=10, le=50)):
    product, _, _ = ctx
    entity = await _get_entity(db, product.id, entity_id)
    cands = await _candidates(db, product.id if scope == "product" else "")
    qtext = embed_text_of(entity.payload)
    ranked = rank(entity.embedding, qtext, cands, top_k, exclude_id=entity.id)
    # displayed % = whole-record semantics (name+title+desc+params serialized);
    # name collision additionally reported as its own signal
    ranked = apply_rerank(entity.payload, ranked,
                          {c["id"]: c["payload"] for c in cands})
    for m in ranked:
        m["name_sim"] = name_similarity(entity.name, m["name"])
    return ranked


VERIFY_SYSTEM = """You judge whether two MCP tools are duplicates (same capability) or
distinct. Answer with STRICT JSON: {"verdict": "duplicate" | "distinct", "reason": "<one sentence>"}."""


@router.get("/reports/duplicates")
async def duplicates(ctx: tuple = Depends(require_member), db: AsyncSession = Depends(get_session),
                     scope: str = Query(default="all", pattern="^(product|all)$"),
                     threshold: float = Query(default=0.0),
                     verify: bool = Query(default=False)):
    product, _, _ = ctx
    from .ai import product_threshold
    cands = await _candidates(db, product.id if scope == "product" else "")
    th = threshold or await product_threshold(db, product.id)
    # cosine casts a wide net (lower floor), the cross-encoder gives the final say
    pairs = duplicate_pairs(cands, max(0.3, th * 0.6))
    pairs = rerank_pairs(pairs, {c["id"]: c["payload"] for c in cands})
    pairs = [p for p in pairs if p["score"] >= th * 0.8] if verify else             [p for p in pairs if p["score"] >= th]
    if verify:
        # LLM verdict on the ambiguous band (research: zero-shot pairwise works well);
        # graceful no-op when no AI gateway is configured
        from .. import llm as llm_mod
        from ..similarity import serialize_tool
        client = await llm_mod.llm_for_product(db, product)
        if client is not None:
            payload_of = {c["id"]: c["payload"] for c in cands}
            band = [p for p in pairs if p["score"] >= 0.2 and
                    (p["score"] < th or p["score"] <= 0.9)][:10]
            for p in band:
                try:
                    raw = await client.complete(VERIFY_SYSTEM,
                        f"Tool A: {serialize_tool(payload_of.get(p['a']['id'], {}))}\n"
                        f"Tool B: {serialize_tool(payload_of.get(p['b']['id'], {}))}")
                    v = llm_mod.extract_json(raw)
                    p["verdict"] = v.get("verdict")
                    p["verdict_reason"] = v.get("reason", "")
                except Exception:
                    pass                                  # verification is best-effort
        pairs = [p for p in pairs if p["score"] >= th or p.get("verdict") == "duplicate"]
    return {"threshold": th, "pairs": pairs}
