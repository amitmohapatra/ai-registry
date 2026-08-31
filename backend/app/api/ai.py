"""Similarity-preview, overlap explain, and per-product similarity settings.
(LLM/Bifrost-assisted generation was removed for now; local models only.)"""

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import config
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
    view: str = "worst"         # which DRAFT view to score: 'worst' (all views,
                                # legacy/API), 'base' (external tab), or an
                                # audience key (that tab checks only its own text)


class SettingsIn(BaseModel):
    similarity_threshold: float = 0.5
    scope: str = "product"          # product: override THIS product (admins);
                                    # global: registry default (super admin only)
    reset: bool = False             # scope=product: drop the override instead
    tuning: dict | None = None      # optional overrides of app.tuning.DEFAULTS


async def registry_default_threshold(db: AsyncSession) -> float:
    """The registry-wide default — what every product uses unless its admins
    override it in Manage → Settings."""
    from ..config import get_settings
    from ..models import GlobalSettings
    row = (await db.execute(select(GlobalSettings))).scalars().first()
    if row and "similarity_threshold" in (row.data or {}):
        return float(row.data["similarity_threshold"])
    return get_settings().similarity_threshold


async def product_threshold(db: AsyncSession, product_id: str = "") -> float:
    """The threshold governing a product's surfaces (its overlap tab, editor
    warnings, suggestions): the product override when its admins set one,
    else the registry default."""
    if product_id:
        from ..models import ProductSettings
        row = (await db.execute(select(ProductSettings).where(
            ProductSettings.product_id == product_id))).scalars().first()
        if row and "similarity_threshold" in (row.data or {}):
            return float(row.data["similarity_threshold"])
    return await registry_default_threshold(db)


# Heavy cross-encoder scoring is pure CPU work. Run it in a worker thread so
# the event loop keeps serving every other request, and serialize the jobs so
# concurrent previews cannot thrash the CPU against each other.
_score_gate = asyncio.Semaphore(config.SCORE_CONCURRENCY)
_preview_cache: "dict[str, dict]" = {}
_preview_inflight: "dict[str, asyncio.Future]" = {}
_PREVIEW_CACHE_CAP = config.PREVIEW_CACHE_CAP


async def registry_tuning(db: AsyncSession) -> dict:
    """All behavioral knobs, meta-driven: defaults from app.tuning merged with
    any overrides the super admin stored in global settings."""
    from ..models import GlobalSettings
    from ..tuning import tuning
    row = (await db.execute(select(GlobalSettings))).scalars().first()
    return tuning((row.data or {}).get("tuning") if row else None)


@router.get("/settings")
async def get_product_settings(ctx: tuple = Depends(require_member),
                               db: AsyncSession = Depends(get_session)):
    from ..models import ProductSettings
    product, _, _ = ctx
    default = await registry_default_threshold(db)
    row = (await db.execute(select(ProductSettings).where(
        ProductSettings.product_id == product.id))).scalars().first()
    has_override = bool(row and "similarity_threshold" in (row.data or {}))
    effective = float(row.data["similarity_threshold"]) if has_override else default
    return {"similarity_threshold": effective, "registry_default": default,
            "overridden": has_override}


@router.put("/settings", status_code=204)
async def set_settings(body: SettingsIn, ctx: tuple = Depends(require_member),
                       db: AsyncSession = Depends(get_session)):
    """scope=product: this product's threshold — its admins may change it.
    scope=global: the registry default (and tuning) — super admin only."""
    from ..models import GlobalSettings, ProductSettings
    product, actor, role = ctx
    if role not in ("admin", "super_admin"):
        raise HTTPException(403, "Only product admins can change the similarity threshold")
    if not body.reset and not (0.05 <= body.similarity_threshold <= 0.99):
        raise HTTPException(422, "similarity_threshold must be between 0.05 and 0.99")

    if body.scope == "product":
        row = (await db.execute(select(ProductSettings).where(
            ProductSettings.product_id == product.id))).scalars().first()
        if body.reset:
            if row and "similarity_threshold" in (row.data or {}):
                row.data = {k: v for k, v in row.data.items() if k != "similarity_threshold"}
        else:
            if not row:
                row = ProductSettings(product_id=product.id)
                db.add(row)
            row.data = {**(row.data or {}), "similarity_threshold": body.similarity_threshold}
        await db.commit()
        await audit(db, actor, "settings.set", "product", product.id,
                    {"similarity_threshold": None if body.reset else body.similarity_threshold})
    else:
        if role != "super_admin":
            raise HTTPException(403, "Only the super admin can change the registry default")
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
    from .entities import schedule_report_warm
    schedule_report_warm()

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
        [payload, threshold, tune, reg_state, body.suggestions, body.view],
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

    def _views_of(p: dict):
        """Every published view of a payload: base + each audience override."""
        views = [("base", p)]
        for aud, ov in (p.get("audiences") or {}).items():
            o = (ov or {}).get("overrides") or {}
            if (ov or {}).get("enabled") is False:
                continue
            if o.get("description") or o.get("title"):
                vp = {**p}
                vp.update({k: o[k] for k in ("description", "title") if o.get(k)})
                views.append((aud, vp))
        return views

    def _score():
        from ..similarity import NoopReranker, blend_breakdown, reranker
        from ..suggestions import build_suggestions
        pl = dict(payload)
        # while-typing tier reranks only the top few — the warning shows the
        # top match; the full net is reserved for suggestion validation
        top_k = int(tune["candidate_top_k"]) if body.suggestions else 3
        matches = rank(vec, text, cands, top_k=top_k, exclude_id=exclude)
        rr = reranker()
        neural = not isinstance(rr, NoopReranker)

        def rescore(drafts):
            """Draft views x candidate views: the WORST combination drives each
            match — a clean base can no longer hide a colliding override on
            EITHER side of the pair."""
            if not neural:
                return apply_rerank(drafts[0][1], [dict(m) for m in matches], by_id), None
            out = []
            for m in matches:
                other = by_id.get(m["id"])
                if other is None:
                    out.append(dict(m))
                    continue
                best = None
                for d_view, d_pl in drafts:
                    for o_view, o_pl in _views_of(other):
                        bd = blend_breakdown(rr, d_pl, o_pl)
                        if best is None or bd["overall"] > best[0]["overall"]:
                            best = (bd, d_view, o_view)
                q = dict(m)
                q["score"] = best[0]["overall"]
                q["breakdown"] = best[0]
                q["match_view"] = best[2]          # which of THEIR views collided
                q["draft_view"] = best[1]          # which of OUR views collided
                out.append(q)
            out.sort(key=lambda x: -x["score"])
            return out, out[0]["draft_view"] if out else None

        draft_views = _views_of(pl)
        if body.view == "base":
            draft_views = draft_views[:1]          # the external tab checks base text
        elif body.view != "worst":
            sel = [v for v in draft_views if v[0] == body.view]
            draft_views = sel or draft_views[:1]   # an audience tab checks its view
        matches, top_draft_view = rescore(draft_views)
        for m in matches:
            m["name_sim"] = name_similarity(pl.get("name", ""), m["name"])
        flagged_audience = top_draft_view if top_draft_view not in (None, "base") else None
        if flagged_audience:                        # suggestions target the worst text
            pl = dict(next(p for v, p in draft_views if v == flagged_audience))

        top_explain = None
        if body.suggestions and matches and matches[0]["score"] >= min(0.4, threshold):
            other_pl = by_id.get(matches[0]["id"])
            if other_pl is not None:
                mv = matches[0].get("match_view")
                other_view = dict(_views_of(other_pl))
                top_explain = {"other": {"id": matches[0]["id"], "name": matches[0]["name"],
                                         "product_key": matches[0]["product_key"]},
                               **explain_pair(pl, other_view.get(mv, other_pl))}
        suggestions = None
        if matches and body.suggestions:
            top = matches[0]
            flagged = (top["score"] >= threshold
                       or (top.get("name_sim") or 0) >= tune["name_collision_sim"])
            if flagged and top["id"] in by_id:
                taken = {c["name"] for c in cands} | {pl.get("name", "")}
                nearby = []
                for m in matches:
                    if m["id"] not in by_id:
                        continue
                    ov = dict(_views_of(by_id[m["id"]]))
                    nearby.append((m["id"], ov.get(m.get("match_view"), by_id[m["id"]])))
                desc_only = body.view not in ("worst", "base")
                suggestions = build_suggestions(
                    pl, by_id[top["id"]], product.key, taken,
                    name_collision=flagged and not desc_only,
                    threshold=threshold, others=[v for _, v in nearby], tune=tune)
                if suggestions and not suggestions.get("packages"):
                    # nothing fixable from THIS side — check whether the fix
                    # lives on the OTHER side (its wording may have drifted);
                    # same engine, roles swapped, same worst-case verification
                    # verify the other side against its neighborhood WITHOUT
                    # itself: scoring its rewrite vs its own old text floors
                    # every candidate at self-similarity
                    other_sugg = build_suggestions(
                        by_id[top["id"]], pl, top["product_key"], taken,
                        name_collision=True, threshold=threshold,
                        others=[pl] + [v for i, v in nearby if i != top["id"]],
                        tune=tune)
                    if other_sugg.get("packages"):
                        suggestions["other_side"] = {
                            "id": top["id"], "name": top["name"],
                            "product_key": top["product_key"],
                            "packages": other_sugg["packages"][:1]}
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

_resolve_cache: dict = {}
_RESOLVE_CACHE_CAP = config.RESOLVE_CACHE_CAP


@router.get("/entities/{entity_id}/resolve/{other_id}")
async def resolve_pair(entity_id: str, other_id: str,
                       ctx: tuple = Depends(require_member),
                       db: AsyncSession = Depends(get_session),
                       aud_a: str = Query(default="", max_length=64),
                       aud_b: str = Query(default="", max_length=64)):
    """The ENGINE's recommendation for a pair — never canned text. Generates
    meaning-preserving candidates for side A, worst-case-verifies them against
    the registry; if A cannot move without changing meaning, tries side B
    (whose wording may have drifted); if neither can, returns the fill-in
    template. Identical machinery and numbers as the editor."""
    import hashlib
    _ = ctx
    a = await db.get(Entity, entity_id)
    b = await db.get(Entity, other_id)
    if not a or a.is_deleted or not b or b.is_deleted:
        raise HTTPException(404, "Entity not found")
    from ..models import Product
    prod_a = await db.get(Product, a.product_id)
    prod_b = await db.get(Product, b.product_id)
    tune = await registry_tuning(db)
    threshold = await product_threshold(db, a.product_id)
    cands = await _candidates(db, "")
    by_id = {c["id"]: c["payload"] for c in cands}
    taken = {c["name"] for c in cands}

    def view(payload: dict, aud: str) -> dict:
        ov = ((payload.get("audiences") or {}).get(aud) or {}).get("overrides") or {}
        if not aud or not ov:
            return payload
        out = {**payload}
        out.update({k: ov[k] for k in ("description", "title") if ov.get(k)})
        return out

    pa, pb = view(a.payload, aud_a), view(b.payload, aud_b)
    key = hashlib.sha256(repr((a.id, a.version, b.id, b.version, aud_a, aud_b,
                               threshold, sorted(tune.items()))).encode()).hexdigest()
    hit = _resolve_cache.get(key)
    if hit is not None:
        return hit

    vec_a, vec_b = a.embedding, b.embedding

    def _work():
        from ..suggestions import build_suggestions
        matches = rank(vec_a, embed_text_of(pa), cands,
                       top_k=int(tune["candidate_top_k"]), exclude_id=a.id)
        nearby = [by_id[m["id"]] for m in matches if m["id"] in by_id]
        s1 = build_suggestions(pa, pb, prod_a.key if prod_a else "", taken,
                               name_collision=True, threshold=threshold,
                               others=[pb] + nearby, tune=tune)
        if s1.get("packages"):
            return {"side": "a", "tool": {"id": a.id, "name": a.name,
                                          "product_key": prod_a.key if prod_a else ""},
                    "current": {"name": a.name, "title": pa.get("title", ""),
                                "description": pa.get("description", "")},
                    "threshold": threshold, "packages": s1["packages"]}
        # side B verifies against ITS OWN neighborhood, never against itself —
        # scoring B's rewrite vs B's old text floors every candidate at
        # self-similarity and inflates the "verified" number
        matches_b = rank(vec_b, embed_text_of(pb), cands,
                         top_k=int(tune["candidate_top_k"]), exclude_id=b.id)
        nearby_b = [by_id[m["id"]] for m in matches_b
                    if m["id"] in by_id and m["id"] != b.id]
        s2 = build_suggestions(pb, pa, prod_b.key if prod_b else "", taken,
                               name_collision=True, threshold=threshold,
                               others=[pa] + nearby_b, tune=tune)
        if s2.get("packages"):
            return {"side": "b", "tool": {"id": b.id, "name": b.name,
                                          "product_key": prod_b.key if prod_b else ""},
                    "current": {"name": b.name, "title": pb.get("title", ""),
                                "description": pb.get("description", "")},
                    "threshold": threshold, "packages": s2["packages"]}
        return {"side": None, "template": s1.get("template"),
                "tool": {"id": a.id, "name": a.name,
                         "product_key": prod_a.key if prod_a else ""}}

    await db.close()
    async with _score_gate:
        out = await asyncio.to_thread(_work)
    if len(_resolve_cache) >= _RESOLVE_CACHE_CAP:
        _resolve_cache.pop(next(iter(_resolve_cache)))
    _resolve_cache[key] = out
    return out


# ---------- pairwise explain (saved entities) ----------

@router.get("/entities/{entity_id}/explain/{other_id}")
async def explain(entity_id: str, other_id: str, ctx: tuple = Depends(require_member),
                  db: AsyncSession = Depends(get_session),
                  aud_a: str = Query(default="", max_length=64),
                  aud_b: str = Query(default="", max_length=64)):
    """aud_a / aud_b select which VIEW of each side to explain — the breakdown
    must describe the same texts the overlap row scored, or the numbers lie."""
    _ = ctx                                        # membership on the current product
    a = await db.get(Entity, entity_id)            # either side may live in another
    b = await db.get(Entity, other_id)             # product (cross-product reports)
    if not a or a.is_deleted or not b or b.is_deleted:
        raise HTTPException(404, "Entity not found")

    def view(payload: dict, aud: str) -> dict:
        ov = ((payload.get("audiences") or {}).get(aud) or {}).get("overrides") or {}
        if not aud or not ov:
            return payload
        out = {**payload}
        out.update({k: ov[k] for k in ("description", "title") if ov.get(k)})
        return out

    pa, pb = view(a.payload, aud_a), view(b.payload, aud_b)
    return {"a": {"id": a.id, "name": a.name}, "b": {"id": b.id, "name": b.name},
            **explain_pair(pa, pb)}
