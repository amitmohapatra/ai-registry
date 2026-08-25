from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..deps import current_user, require_super
from ..models import User
from ..schemas import LoginIn, TokenOut, UserIn, UserOut
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


@router.get("/users", response_model=list[UserOut])
async def list_users(db: AsyncSession = Depends(get_session), _: User = Depends(require_super)):
    return (await db.execute(select(User).order_by(User.email))).scalars().all()
