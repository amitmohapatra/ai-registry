"""Similarity-preview, overlap explain, and per-product similarity settings.
(LLM/Bifrost-assisted generation was removed for now; local models only.)"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..deps import require_member
from ..models import Entity
from ..services import audit, embedder
from ..similarity import apply_rerank, embed_text_of, explain_pair, name_similarity, rank
from .entities import _candidates

router = APIRouter(prefix="/v1/products/{product_key}", tags=["ai"])


class DraftIn(BaseModel):
    type: str = "tool"
    payload: dict


class SettingsIn(BaseModel):
    similarity_threshold: float = 0.5


async def product_threshold(db: AsyncSession, product_id: str = "") -> float:
    """ONE threshold for the whole registry — every surface (overlap report,
    editor warnings, suggestions) reads this same value."""
    from ..config import get_settings
    from ..models import GlobalSettings
    row = (await db.execute(select(GlobalSettings))).scalars().first()
    if row and "similarity_threshold" in (row.data or {}):
        return float(row.data["similarity_threshold"])
    return get_settings().similarity_threshold


@router.get("/settings", response_model=SettingsIn)
async def get_product_settings(ctx: tuple = Depends(require_member),
                               db: AsyncSession = Depends(get_session)):
    product, _, _ = ctx
    return SettingsIn(similarity_threshold=await product_threshold(db, product.id))


@router.put("/settings", status_code=204)
async def set_settings(body: SettingsIn, ctx: tuple = Depends(require_member),
                       db: AsyncSession = Depends(get_session)):
    """Registry-wide: only the super admin can change it (it affects every product)."""
    from ..models import GlobalSettings
    product, actor, role = ctx
    if role != "super_admin":
        raise HTTPException(403, "Only the super admin can change the similarity threshold")
    if not (0.05 <= body.similarity_threshold <= 0.99):
        raise HTTPException(422, "similarity_threshold must be between 0.05 and 0.99")
    row = (await db.execute(select(GlobalSettings))).scalars().first()
    if not row:
        row = GlobalSettings(id=1)
        db.add(row)
    row.data = {**(row.data or {}), "similarity_threshold": body.similarity_threshold}
    await db.commit()
    await audit(db, actor, "settings.set", "global", product.id,
                {"similarity_threshold": body.similarity_threshold})

# ---------- similarity preview (pre-save) ----------

@router.post("/entities/similar-preview")
async def similar_preview(body: DraftIn, ctx: tuple = Depends(require_member),
                          db: AsyncSession = Depends(get_session)):
    """Match a DRAFT payload against everything in the registry before it is saved.
    Returns top matches with %, plus the overlap breakdown for the best match."""
    product, _, _ = ctx
    payload = body.payload or {}
    text = embed_text_of(payload)
    if not text.strip():
        return {"matches": [], "top_explain": None,
                "threshold": await product_threshold(db, product.id)}
    vec = (await embedder().embed([text]))[0]
    cands = await _candidates(db, "")
    exclude = payload.get("_entity_id")            # editing an existing tool: skip itself
    matches = rank(vec, text, cands, top_k=10, exclude_id=exclude)
    matches = apply_rerank(payload, matches,
                           {c["id"]: c["payload"] for c in cands})
    for m in matches:
        m["name_sim"] = name_similarity(payload.get("name", ""), m["name"])
    threshold = await product_threshold(db, product.id)
    top_explain = None
    if matches and matches[0]["score"] >= min(0.4, threshold):
        other = await db.get(Entity, matches[0]["id"])
        if other:
            top_explain = {"other": {"id": other.id, "name": other.name,
                                     "product_key": matches[0]["product_key"]},
                           **explain_pair(payload, other.payload)}
    suggestions = None
    if matches:
        top = matches[0]
        flagged = top["score"] >= threshold or (top.get("name_sim") or 0) >= 0.8
        if flagged:
            from ..suggestions import build_suggestions
            other = await db.get(Entity, top["id"])
            if other:
                taken = {c["name"] for c in cands} | {payload.get("name", "")}
                by_id = {c["id"]: c["payload"] for c in cands}
                nearby = [by_id[m["id"]] for m in matches if m["id"] in by_id]
                suggestions = build_suggestions(
                    payload, other.payload, product.key, taken,
                    name_collision=(top.get("name_sim") or 0) >= 0.8 or top["score"] >= threshold,
                    threshold=threshold, others=nearby)
    return {"matches": matches, "top_explain": top_explain, "threshold": threshold,
            "suggestions": suggestions}

# ---------- pairwise explain (saved entities) ----------

@router.get("/entities/{entity_id}/explain/{other_id}")
async def explain(entity_id: str, other_id: str, ctx: tuple = Depends(require_member),
                  db: AsyncSession = Depends(get_session)):
    _ = ctx                                        # membership on the current product
    a = await db.get(Entity, entity_id)            # either side may live in another
    b = await db.get(Entity, other_id)             # product (cross-product reports)
    if not a or a.is_deleted or not b or b.is_deleted:
        raise HTTPException(404, "Entity not found")
    return {"a": {"id": a.id, "name": a.name}, "b": {"id": b.id, "name": b.name},
            **explain_pair(a.payload, b.payload)}
