"""LLM access via an OpenAI-compatible gateway (Bifrost). The registry never talks
to model vendors directly: Bifrost handles routing, budgets and virtual keys.
No config -> every AI feature is simply absent; deterministic features unaffected."""
import json
import re
from typing import Optional

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from .crypto import decrypt_json
from .models import Product


class LLMClient:
    def __init__(self, base_url: str, api_key: str, model: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model

    async def complete(self, system: str, user: str, max_tokens: int = 1024) -> str:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(f"{self.base_url}/chat/completions",
                                  headers={"Authorization": f"Bearer {self.api_key}"},
                                  json={"model": self.model, "max_tokens": max_tokens,
                                        "messages": [{"role": "system", "content": system},
                                                     {"role": "user", "content": user}]})
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]


async def llm_for_product(db: AsyncSession, product: Product) -> Optional[LLMClient]:
    """Per-product Bifrost config first (its own virtual key/budget), else the
    installation-wide default from env. None if neither is configured."""
    from .models import AiConfig
    row = (await db.execute(select(AiConfig).where(
        AiConfig.product_id == product.id))).scalars().first()
    if row:
        cfg = decrypt_json(row.config_enc)
        if cfg.get("base_url") and cfg.get("api_key"):
            return LLMClient(cfg["base_url"], cfg["api_key"],
                             cfg.get("model") or "anthropic/claude-sonnet-4-5")
    st = get_settings()
    if st.bifrost_url and st.bifrost_key:
        return LLMClient(st.bifrost_url, st.bifrost_key, st.llm_model)
    return None


def extract_json(text: str) -> dict:
    """LLMs wrap JSON in prose/fences; dig it out defensively."""
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S) or re.search(r"(\{.*\})", text, re.S)
    if not m:
        raise ValueError("no JSON object in LLM response")
    return json.loads(m.group(1))
