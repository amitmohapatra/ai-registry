"""Similarity-preview, overlap explain, per-product Bifrost config, and AI-assisted
generation. All LLM traffic goes through Bifrost (OpenAI-compatible, virtual keys)."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import llm as llm_mod
from ..crypto import decrypt_json, encrypt_json
from ..db import get_session
from ..deps import get_product, require_member, require_product_admin, require_super
from ..models import AiConfig, Entity, Product, User
from ..services import audit, embedder
from ..similarity import apply_rerank, embed_text_of, explain_pair, rank
from .entities import _candidates, _get_entity

router = APIRouter(prefix="/v1/products/{product_key}", tags=["ai"])


class DraftIn(BaseModel):
    type: str = "tool"
    payload: dict
    instruction: str = ""      # optional steering, e.g. "differentiate from similar tools"


class AiConfigIn(BaseModel):
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    generate_prompt: str = ""   # optional override, e.g. synced from the Bifrost prompt store
    explain_prompt: str = ""


class SettingsIn(BaseModel):
    similarity_threshold: float = 0.5


async def product_threshold(db: AsyncSession, product_id: str) -> float:
    from ..config import get_settings
    from ..models import ProductSettings
    row = (await db.execute(select(ProductSettings).where(
        ProductSettings.product_id == product_id))).scalars().first()
    if row and "similarity_threshold" in (row.data or {}):
        return float(row.data["similarity_threshold"])
    return get_settings().similarity_threshold


@router.get("/settings", response_model=SettingsIn)
async def get_product_settings(ctx: tuple = Depends(require_member),
                               db: AsyncSession = Depends(get_session)):
    product, _, _ = ctx
    return SettingsIn(similarity_threshold=await product_threshold(db, product.id))


@router.put("/settings", status_code=204)
async def set_product_settings(body: SettingsIn, ctx: tuple = Depends(require_product_admin),
                               db: AsyncSession = Depends(get_session)):
    from ..models import ProductSettings
    product, actor, _ = ctx
    if not (0.05 <= body.similarity_threshold <= 0.99):
        raise HTTPException(422, "similarity_threshold must be between 0.05 and 0.99")
    row = (await db.execute(select(ProductSettings).where(
        ProductSettings.product_id == product.id))).scalars().first()
    if not row:
        row = ProductSettings(product_id=product.id)
        db.add(row)
    row.data = {**(row.data or {}), "similarity_threshold": body.similarity_threshold}
    await db.commit()
    await audit(db, actor, "settings.set", product.key, product.id,
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
    matches = rank(vec, text, cands, top_k=5, exclude_id=exclude)
    matches = apply_rerank(text, matches, {c["id"]: c["text"] for c in cands})
    threshold = await product_threshold(db, product.id)
    top_explain = None
    if matches and matches[0]["score"] >= min(0.4, threshold):
        other = await db.get(Entity, matches[0]["id"])
        if other:
            top_explain = {"other": {"id": other.id, "name": other.name,
                                     "product_key": matches[0]["product_key"]},
                           **explain_pair(payload, other.payload)}
    return {"matches": matches, "top_explain": top_explain, "threshold": threshold}

# ---------- pairwise explain (saved entities) ----------

@router.get("/entities/{entity_id}/explain/{other_id}")
async def explain(entity_id: str, other_id: str, ctx: tuple = Depends(require_member),
                  db: AsyncSession = Depends(get_session)):
    product, _, _ = ctx
    a = await _get_entity(db, product.id, entity_id)
    b = await db.get(Entity, other_id)             # may live in another product
    if not b or b.is_deleted:
        raise HTTPException(404, "Other entity not found")
    return {"a": {"id": a.id, "name": a.name}, "b": {"id": b.id, "name": b.name},
            **explain_pair(a.payload, b.payload)}

# ---------- per-product Bifrost config ----------

@router.put("/ai-config", status_code=204)
async def set_ai_config(body: AiConfigIn, product: Product = Depends(get_product),
        db: AsyncSession = Depends(get_session), actor: User = Depends(require_super)):
    row = (await db.execute(select(AiConfig).where(AiConfig.product_id == product.id))).scalars().first()
    if not row:
        row = AiConfig(product_id=product.id)
        db.add(row)
    row.config_enc = encrypt_json(body.model_dump())
    await db.commit()
    await audit(db, actor, "aiconfig.set", product.key, product.id)


@router.get("/ai-config", response_model=AiConfigIn)
async def get_ai_config(product: Product = Depends(get_product),
        db: AsyncSession = Depends(get_session), _: User = Depends(require_super)):
    row = (await db.execute(select(AiConfig).where(AiConfig.product_id == product.id))).scalars().first()
    return AiConfigIn(**(decrypt_json(row.config_enc) if row else {}))


@router.get("/ai-config/status")
async def ai_status(ctx: tuple = Depends(require_member), db: AsyncSession = Depends(get_session)):
    """Members only learn WHETHER AI is available, never the key."""
    product, _, _ = ctx
    client = await llm_mod.llm_for_product(db, product)
    return {"configured": client is not None, "model": client.model if client else None}

# ---------- AI-assisted generation & explanation (via Bifrost) ----------

async def _prompt(db: AsyncSession, product: Product, field: str, default: str) -> str:
    """Prompt resolution: per-product override (authored/versioned in the Bifrost
    prompt store and pasted/synced here) -> built-in default."""
    row = (await db.execute(select(AiConfig).where(
        AiConfig.product_id == product.id))).scalars().first()
    if row:
        text = (decrypt_json(row.config_enc) or {}).get(field, "")
        if text.strip():
            return text
    return default


GENERATE_SYSTEM = """You improve metadata for MCP tools in a company tool registry.
Given a draft tool (name, parameters, maybe a rough description) and similar existing
tools, return STRICT JSON: {"description": str, "title": str,
"param_descriptions": {param_name: str}}. The description must be 1-3 sentences,
model-facing (it tells an AI when and how to use the tool), concrete about scope,
and clearly distinguished from the similar tools listed."""


@router.post("/entities/ai/generate")
async def ai_generate(body: DraftIn, ctx: tuple = Depends(require_product_admin),
                      db: AsyncSession = Depends(get_session)):
    product, actor, _ = ctx
    client = await llm_mod.llm_for_product(db, product)
    if client is None:
        raise HTTPException(400, "No AI gateway configured (set Bifrost URL + virtual key)")
    payload = body.payload or {}
    text = embed_text_of(payload)
    similar_txt = ""
    if text.strip():
        vec = (await embedder().embed([text]))[0]
        cands = await _candidates(db, "")
        for m in rank(vec, text, cands, top_k=3, exclude_id=payload.get("_entity_id")):
            other = await db.get(Entity, m["id"])
            if other:
                similar_txt += f"- {m['product_key']}/{other.name}: {other.payload.get('description','')}\n"
    import json as _json
    user_msg = (f"Draft tool:\n{_json.dumps({k: payload.get(k) for k in ('name', 'title', 'description', 'input_schema')}, indent=2)}\n\n"
                f"Similar existing tools (be different from these):\n{similar_txt or '- none'}")
    if body.instruction:
        user_msg += f"\n\nSpecial instruction: {body.instruction}"
    import httpx as _httpx
    try:
        raw = await client.complete(await _prompt(db, product, "generate_prompt",
                                                  GENERATE_SYSTEM), user_msg)
    except _httpx.HTTPError as exc:
        raise HTTPException(502, f"AI gateway error: {exc}")
    try:
        suggestion = llm_mod.extract_json(raw)
    except ValueError:
        raise HTTPException(502, "AI returned an unparseable response; try again")
    await audit(db, actor, "ai.generate", payload.get("name", ""), product.id)
    return {"suggestion": {"description": suggestion.get("description", ""),
                           "title": suggestion.get("title", ""),
                           "param_descriptions": suggestion.get("param_descriptions", {})},
            "model": client.model}


EXPLAIN_SYSTEM = """You analyze two MCP tools from a company tool registry that scored
high on similarity. In 3-5 sentences: are they true duplicates or legitimately distinct,
which should be canonical if duplicates, and what specific wording changes would
differentiate them. Be direct and specific. Plain text, no JSON."""


@router.post("/entities/{entity_id}/explain/{other_id}/ai")
async def ai_explain(entity_id: str, other_id: str, ctx: tuple = Depends(require_member),
                     db: AsyncSession = Depends(get_session)):
    product, _, _ = ctx
    client = await llm_mod.llm_for_product(db, product)
    if client is None:
        raise HTTPException(400, "No AI gateway configured")
    a = await _get_entity(db, product.id, entity_id)
    b = await db.get(Entity, other_id)
    if not b or b.is_deleted:
        raise HTTPException(404, "Other entity not found")
    import json as _json
    user_msg = (f"Tool A:\n{_json.dumps({'name': a.name, 'description': a.payload.get('description'), 'params': list((a.payload.get('input_schema') or {}).get('properties', {}))})}\n\n"
                f"Tool B:\n{_json.dumps({'name': b.name, 'description': b.payload.get('description'), 'params': list((b.payload.get('input_schema') or {}).get('properties', {}))})}")
    import httpx as _httpx
    try:
        return {"analysis": await client.complete(await _prompt(db, product, "explain_prompt",
                                                               EXPLAIN_SYSTEM), user_msg),
                "model": client.model}
    except _httpx.HTTPError as exc:
        raise HTTPException(502, f"AI gateway error: {exc}")
