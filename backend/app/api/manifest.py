"""Data-plane endpoints consumed by the MCP SDK (API-key auth, not user JWT).

Two push options for SDKs:
- Redis pub/sub (when the super admin configured a channel for the product)
- /events SSE stream served straight off the registry's bus (zero-infra fallback)
"""
import asyncio
import json

from fastapi import APIRouter, Depends, Response
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ..crypto import decrypt_json
from ..db import get_session
from ..deps import product_from_api_key
from ..events import bus_router
from ..models import Product
from ..services import cached_manifest

router = APIRouter(prefix="/v1/products/{product_key}", tags=["manifest"])


@router.get("/manifest")
async def manifest(response: Response, product: Product = Depends(product_from_api_key),
                   db: AsyncSession = Depends(get_session)):
    m = await cached_manifest(db, product)
    response.headers["ETag"] = f'W/"{m["seq"]}"'
    return m


@router.get("/events")
async def events(product: Product = Depends(product_from_api_key),
                 db: AsyncSession = Depends(get_session)):
    channel = decrypt_json(product.channel_config_enc)
    name = f"{channel.get('channel_prefix', 'registry')}:{product.key}"

    async def stream():
        yield f"event: hello\ndata: {json.dumps({'seq': product.seq})}\n\n"
        async for message in bus_router.default.subscribe(name):
            yield f"event: change\ndata: {json.dumps(message)}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache"})
