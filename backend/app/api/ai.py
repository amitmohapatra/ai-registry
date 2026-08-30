"""Similarity-preview, overlap explain, and per-product similarity settings.
(LLM/Bifrost-assisted generation was removed for now; local models only.)"""

import asyncio

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
    suggestions: bool = True    # False = cheap while-typing check (matches only)


class SettingsIn(BaseModel):
    similarity_threshold: float = 0.5
    tuning: dict | None = None      # optional overrides of app.tuning.DEFAULTS


async def product_threshold(db: AsyncSession, product_id: str = "") -> float:
    """ONE threshold for the whole registry — every surface (overlap report,
    editor warnings, suggestions) reads this same value."""
    from ..config import get_settings
    from ..models import GlobalSettings
    row = (await db.execute(select(GlobalSettings))).scalars().first()
    if row and "similarity_threshold" in (row.data or {}):
        return float(row.data["similarity_threshold"])
    return get_settings().similarity_threshold


# Heavy cross-encoder scoring is pure CPU work. Run it in a worker thread so
# the event loop keeps serving every other request, and serialize the jobs so
# concurrent previews cannot thrash the CPU against each other.
_score_gate = asyncio.Semaphore(1)
_preview_cache: "dict[str, dict]" = {}
_preview_inflight: "dict[str, asyncio.Future]" = {}
_PREVIEW_CACHE_CAP = 256


async def registry_tuning(db: AsyncSession) -> dict:
    """All behavioral knobs, meta-driven: defaults from app.tuning merged with
    any overrides the super admin stored in global settings."""
    from ..models import GlobalSettings
    from ..tuning import tuning
    row = (await db.execute(select(GlobalSettings))).scalars().first()
    return tuning((row.data or {}).get("tuning") if row else None)


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
    from ..tuning import DEFAULTS
    patch = {"similarity_threshold": body.similarity_threshold}
    if body.tuning is not None:
        unknown = set(body.tuning) - set(DEFAULTS)
        if unknown:
            raise HTTPException(422, f"Unknown tuning keys: {', '.join(sorted(unknown))}")
        patch["tuning"] = body.tuning
    row.data = {**(row.data or {}), **patch}
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
    cands = await _candidates(db, "")
    exclude = payload.get("_entity_id")            # editing an existing tool: skip itself
    tune = await registry_tuning(db)
    threshold = await product_threshold(db, product.id)
    by_id = {c["id"]: c["payload"] for c in cands}

    import hashlib
    import json as _json
    reg_state = sorted((c["id"], c.get("v")) for c in cands)
    cache_key = hashlib.sha256(_json.dumps(
        [payload, threshold, tune, reg_state, body.suggestions],
        sort_keys=True, default=str).encode()).hexdigest()
    hit = _preview_cache.get(cache_key)
    if hit is not None:
        return hit
    inflight = _preview_inflight.get(cache_key)
    if inflight is not None:                       # identical preview already computing
        return await asyncio.shield(inflight)
    fut = asyncio.get_running_loop().create_future()
    _preview_inflight[cache_key] = fut

    vec = (await embedder().embed([text]))[0]

    def _score():
        from ..suggestions import build_suggestions
        pl = dict(payload)
        # while-typing tier reranks only the top few — the warning shows the
        # top match; the full net is reserved for suggestion validation
        top_k = int(tune["candidate_top_k"]) if body.suggestions else 3
        matches = rank(vec, text, cands, top_k=top_k, exclude_id=exclude)
        matches = apply_rerank(pl, matches, by_id)
        for m in matches:
            m["name_sim"] = name_similarity(pl.get("name", ""), m["name"])

        # audience overlays can override description/title — an audience's text
        # can collide even when the base text is clean, so the WORST variant drives
        flagged_audience = None
        for aud_key, ov in (pl.get("audiences") or {}).items():
            o = (ov or {}).get("overrides") or {}
            if not (o.get("description") or o.get("title")):
                continue
            variant = {**pl}
            variant.update({k: o[k] for k in ("description", "title") if o.get(k)})
            v_matches = apply_rerank(variant, [dict(m) for m in matches], by_id)
            if v_matches and matches and v_matches[0]["score"] > matches[0]["score"]:
                matches = v_matches
                pl = variant                        # suggestions target the worst text
                flagged_audience = aud_key

        top_explain = None
        if body.suggestions and matches and matches[0]["score"] >= min(0.4, threshold):
            other_pl = by_id.get(matches[0]["id"])
            if other_pl is not None:
                top_explain = {"other": {"id": matches[0]["id"], "name": matches[0]["name"],
                                         "product_key": matches[0]["product_key"]},
                               **explain_pair(pl, other_pl)}
        suggestions = None
        if matches and body.suggestions:
            top = matches[0]
            flagged = (top["score"] >= threshold
                       or (top.get("name_sim") or 0) >= tune["name_collision_sim"])
            if flagged and top["id"] in by_id:
                taken = {c["name"] for c in cands} | {pl.get("name", "")}
                nearby = [by_id[m["id"]] for m in matches if m["id"] in by_id]
                suggestions = build_suggestions(
                    pl, by_id[top["id"]], product.key, taken,
                    name_collision=flagged,
                    threshold=threshold, others=nearby, tune=tune)
        return matches, top_explain, flagged_audience, suggestions

    # all DB reads are done — release the pooled connection BEFORE the heavy
    # CPU work, or queued previews starve every other endpoint of connections
    await db.close()
    try:
        async with _score_gate:                    # one heavy job at a time
            matches, top_explain, flagged_audience, suggestions = \
                await asyncio.to_thread(_score)
        out = {"matches": matches, "top_explain": top_explain, "threshold": threshold,
               "flagged_audience": flagged_audience, "suggestions": suggestions}
        if len(_preview_cache) >= _PREVIEW_CACHE_CAP:
            _preview_cache.pop(next(iter(_preview_cache)))
        _preview_cache[cache_key] = out
        fut.set_result(out)
        return out
    except BaseException as exc:
        if not fut.done():
            fut.set_exception(exc)
        raise
    finally:
        _preview_inflight.pop(cache_key, None)

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
