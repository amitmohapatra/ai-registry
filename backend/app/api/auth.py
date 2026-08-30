from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, or_, select

from .. import config
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..deps import current_user, require_super
from ..models import User
from ..models import Membership, Product
from ..schemas import LoginIn, TokenOut, UserAdminOut, UserIn, UserOut, UserPatch
from ..services import audit
from ..security import hash_password, make_token, verify_password

router = APIRouter(prefix="/v1/auth", tags=["auth"])


@router.post("/login", response_model=TokenOut)
async def login(body: LoginIn, db: AsyncSession = Depends(get_session)):
    user = (await db.execute(select(User).where(User.email == body.email))).scalars().first()
    if not user or not user.is_active or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "Invalid credentials")
    return TokenOut(access_token=make_token({"sub": user.id, "email": user.email}),
                    user=UserOut.model_validate(user))


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(current_user)):
    return user


@router.post("/users", response_model=UserOut, status_code=201)
async def create_user(body: UserIn, db: AsyncSession = Depends(get_session),
                      _: User = Depends(require_super)):
    exists = (await db.execute(select(User).where(User.email == body.email))).scalars().first()
    if exists:
        raise HTTPException(409, "Email already registered")
    user = User(email=body.email, name=body.name, password_hash=hash_password(body.password),
                is_super_admin=body.is_super_admin)
    db.add(user)
    await db.commit()
    return user


@router.get("/users", response_model=list[UserAdminOut])
async def list_users(response: Response, db: AsyncSession = Depends(get_session),
                     _: User = Depends(require_super),
                     q: str = Query(default=""),
                     product: str = Query(default=""),
                     limit: int = Query(default=config.PAGE_DEFAULT, le=config.PAGE_MAX),
                     offset: int = Query(default=0, ge=0)):
    """The People console: each row carries the account AND its product
    memberships, so the super admin manages everything from one place."""
    stmt = select(User)
    if q:
        stmt = stmt.where(or_(User.email.ilike(f"%{q}%"), User.name.ilike(f"%{q}%")))
    if product:                       # only accounts with a role on this product
        stmt = (stmt.join(Membership, Membership.user_id == User.id)
                    .join(Product, Product.id == Membership.product_id)
                    .where(Product.key == product))
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar()
    response.headers["X-Total-Count"] = str(total)
    users = (await db.execute(stmt.order_by(User.email).limit(limit).offset(offset))).scalars().all()
    ids = [u.id for u in users]
    rows = (await db.execute(
        select(Membership, Product).join(Product, Product.id == Membership.product_id)
        .where(Membership.user_id.in_(ids)))).all() if ids else []
    by_user: dict = {}
    for m, p in rows:
        by_user.setdefault(m.user_id, []).append(
            {"product_key": p.key, "product_name": p.name, "role": m.role})
    return [UserAdminOut(**UserOut.model_validate(u).model_dump(),
                         memberships=sorted(by_user.get(u.id, []), key=lambda x: x["product_key"]))
            for u in users]


@router.patch("/users/{user_id}", response_model=UserOut)
async def update_user(user_id: str, body: UserPatch,
                      db: AsyncSession = Depends(get_session),
                      actor: User = Depends(require_super)):
    """Promote/demote super admin, activate/deactivate, rename. Guarded so
    the platform can never lose its last active super admin — demote the
    bootstrap account only after promoting someone else."""
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(404, "User not found")
    losing_super = ((body.is_super_admin is False or body.is_active is False)
                    and user.is_super_admin and user.is_active)
    if losing_super:
        from sqlalchemy import and_
        others = (await db.execute(select(func.count()).select_from(User).where(and_(
            User.is_super_admin == True, User.is_active == True, User.id != user.id)))).scalar()  # noqa: E712
        if not others:
            raise HTTPException(422, "This is the last active super admin — promote someone else first")
    changes = {}
    for field in ("name", "is_super_admin", "is_active"):
        val = getattr(body, field)
        if val is not None and val != getattr(user, field):
            changes[field] = val
            setattr(user, field, val)
    await db.commit()
    if changes:
        await audit(db, actor, "user.update", user.email, "", changes)
    return user


class DirectoryOut(BaseModel):
    email: str
    name: str
    model_config = {"from_attributes": True}


@router.get("/users/directory", response_model=list[DirectoryOut])
async def user_directory(response: Response, db: AsyncSession = Depends(get_session),
                         _: User = Depends(current_user),
                         q: str = Query(default=""),
                         limit: int = Query(default=20, le=config.PAGE_MAX),
                         offset: int = Query(default=0, ge=0)):
    """Email + name only — lets product admins PICK members from existing
    accounts instead of typing emails blind. No roles or ids exposed.
    Searched and paginated server-side so the picker scales to any org."""
    stmt = select(User)
    if q:
        stmt = stmt.where(or_(User.email.ilike(f"%{q}%"), User.name.ilike(f"%{q}%")))
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar()
    response.headers["X-Total-Count"] = str(total)
    return (await db.execute(stmt.order_by(User.email).limit(limit).offset(offset))).scalars().all()
