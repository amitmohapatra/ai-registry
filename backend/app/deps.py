"""Auth + RBAC dependencies. Roles: super_admin > product admin > product user."""
from typing import Optional

from fastapi import Depends, Header, HTTPException, Path
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .db import get_session
from .models import ApiKey, Membership, Product, User
from .security import hash_api_key, read_token


async def current_user(db: AsyncSession = Depends(get_session),
                       authorization: str = Header(default="")) -> User:
    token = authorization.removeprefix("Bearer ").strip()
    claims = read_token(token) if token else None
    if not claims:
        raise HTTPException(401, "Not authenticated")
    user = await db.get(User, claims.get("sub", ""))
    if not user or not user.is_active:
        raise HTTPException(401, "Account disabled or missing")
    return user


async def require_super(user: User = Depends(current_user)) -> User:
    if not user.is_super_admin:
        raise HTTPException(403, "Super admin only")
    return user


async def get_product(product_key: str = Path(...),
                      db: AsyncSession = Depends(get_session)) -> Product:
    product = (await db.execute(select(Product).where(Product.key == product_key))).scalars().first()
    if not product or not product.is_active:
        raise HTTPException(404, "Product not found")
    return product


async def role_on(db: AsyncSession, user: User, product: Product) -> Optional[str]:
    if user.is_super_admin:
        return "super_admin"
    m = (await db.execute(select(Membership).where(
        Membership.user_id == user.id, Membership.product_id == product.id))).scalars().first()
    return m.role if m else None


async def require_member(product: Product = Depends(get_product),
                         user: User = Depends(current_user),
                         db: AsyncSession = Depends(get_session)) -> tuple:
    role = await role_on(db, user, product)
    if role is None:
        raise HTTPException(403, "No access to this product")
    return product, user, role


async def require_product_admin(ctx: tuple = Depends(require_member)) -> tuple:
    product, user, role = ctx
    if role not in ("admin", "super_admin"):
        raise HTTPException(403, "Product admin only")
    return product, user, role


async def product_from_api_key(product_key: str = Path(...),
                               x_api_key: str = Header(default=""),
                               db: AsyncSession = Depends(get_session)) -> Product:
    """Data-plane auth: SDKs authenticate with a product API key."""
    if not x_api_key:
        raise HTTPException(401, "Missing X-API-Key")
    product = (await db.execute(select(Product).where(Product.key == product_key))).scalars().first()
    if not product or not product.is_active:
        raise HTTPException(404, "Product not found")
    key = (await db.execute(select(ApiKey).where(
        ApiKey.product_id == product.id,
        ApiKey.key_hash == hash_api_key(x_api_key),
        ApiKey.revoked == False))).scalars().first()  # noqa: E712
    if not key:
        raise HTTPException(401, "Invalid or revoked API key")
    return product
