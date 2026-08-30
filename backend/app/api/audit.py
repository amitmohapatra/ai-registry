from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import config
from ..db import get_session
from ..deps import require_member
from ..models import AuditLog

router = APIRouter(prefix="/v1/products/{product_key}/audit", tags=["audit"])


@router.get("")
async def list_audit(ctx: tuple = Depends(require_member), db: AsyncSession = Depends(get_session),
                     limit: int = Query(default=config.PAGE_DEFAULT, le=config.PAGE_MAX), offset: int = Query(default=0, ge=0)):
    product, _, _ = ctx
    rows = (await db.execute(select(AuditLog).where(AuditLog.product_id == product.id)
                             .order_by(AuditLog.created_at.desc())
                             .limit(limit).offset(offset))).scalars().all()
    return [{"actor": r.actor_email, "action": r.action, "target": r.target,
             "detail": r.detail, "at": r.created_at.isoformat()} for r in rows]
