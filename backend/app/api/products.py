from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..crypto import decrypt_json, encrypt_json
from ..db import get_session
from ..deps import current_user, get_product, require_member, require_product_admin, require_super, role_on
from ..models import ApiKey, Audience, Membership, Product, User
from ..schemas import (ApiKeyCreated, ApiKeyOut, AudienceIn, AudienceOut, ChannelConfigIn,
                       MemberIn, MemberOut, ProductIn, ProductOut)
from ..security import new_api_key
from ..services import audit, publish_change

router = APIRouter(prefix="/v1/products", tags=["products"])


def _product_out(product: Product, role: str) -> ProductOut:
    return ProductOut(**{c: getattr(product, c) for c in
                         ("id", "key", "name", "description", "seq", "is_active")}, role=role)


@router.post("", response_model=ProductOut, status_code=201)
async def create_product(body: ProductIn, db: AsyncSession = Depends(get_session),
                         actor: User = Depends(require_super)):
    if (await db.execute(select(Product).where(Product.key == body.key))).scalars().first():
        raise HTTPException(409, "Product key already exists")
    product = Product(key=body.key, name=body.name, description=body.description)
    db.add(product)
    await db.flush()
    db.add(Audience(product_id=product.id, key="external", display_name="External", is_default=True))
    await db.commit()
    await audit(db, actor, "product.create", body.key, product.id)
    return _product_out(product, "super_admin")


@router.get("", response_model=list[ProductOut])
async def list_products(db: AsyncSession = Depends(get_session), user: User = Depends(current_user)):
    """Role-scoped: super admin sees all, others see their memberships."""
    out = []
    for p in (await db.execute(select(Product).where(Product.is_active == True))).scalars().all():  # noqa: E712
        role = await role_on(db, user, p)
        if role:
            out.append(_product_out(p, role))
    return out


@router.get("/{product_key}", response_model=ProductOut)
async def get_one(ctx: tuple = Depends(require_member)):
    product, _, role = ctx
    return _product_out(product, role)


@router.delete("/{product_key}", status_code=204)
async def delete_product(product: Product = Depends(get_product),
                         db: AsyncSession = Depends(get_session),
                         actor: User = Depends(require_super)):
    """Hard delete (super admin): removes the product with its entities, versions,
    audiences, memberships, API keys and settings. The key becomes reusable.
    Audit history is kept (audit rows reference the product by id, not FK).
    Running MCP servers of this product keep serving from memory/snapshot but
    lose registry access — decommission them separately."""
    from ..models import AiConfig, ProductSettings
    for model in (ApiKey, AiConfig, ProductSettings):
        for row in (await db.execute(select(model).where(
                model.product_id == product.id))).scalars().all():
            await db.delete(row)
    key, pid = product.key, product.id
    await db.delete(product)          # cascades: entities+versions, audiences, memberships
    await db.commit()
    await audit(db, actor, "product.delete", key, pid)

# ---- channel config (super admin sets each product's Redis) ----

@router.put("/{product_key}/channel", status_code=204)
async def set_channel(body: ChannelConfigIn, product: Product = Depends(get_product),
                      db: AsyncSession = Depends(get_session),
                      actor: User = Depends(require_super)):
    product.channel_config_enc = encrypt_json(body.model_dump())
    await db.commit()
    await audit(db, actor, "product.channel.set", product.key, product.id)
    await publish_change(db, product, "manifest.reload", {})


@router.get("/{product_key}/channel", response_model=ChannelConfigIn)
async def get_channel(product: Product = Depends(get_product),
                      _: User = Depends(require_super)):
    return ChannelConfigIn(**(decrypt_json(product.channel_config_enc) or {}))

# ---- members ----

@router.put("/{product_key}/members", response_model=MemberOut)
async def upsert_member(body: MemberIn, ctx: tuple = Depends(require_product_admin),
                        db: AsyncSession = Depends(get_session)):
    product, actor, _ = ctx
    user = (await db.execute(select(User).where(User.email == body.email))).scalars().first()
    if not user:
        raise HTTPException(404, "No user with that email — create the account first")
    m = (await db.execute(select(Membership).where(
        Membership.user_id == user.id, Membership.product_id == product.id))).scalars().first()
    if m:
        m.role = body.role
    else:
        db.add(Membership(user_id=user.id, product_id=product.id, role=body.role))
    await db.commit()
    await audit(db, actor, "member.upsert", body.email, product.id, {"role": body.role})
    return MemberOut(user_id=user.id, email=user.email, name=user.name, role=body.role)


@router.get("/{product_key}/members", response_model=list[MemberOut])
async def list_members(ctx: tuple = Depends(require_member), db: AsyncSession = Depends(get_session)):
    product, _, _ = ctx
    rows = (await db.execute(select(Membership, User).join(User, Membership.user_id == User.id)
                             .where(Membership.product_id == product.id))).all()
    return [MemberOut(user_id=u.id, email=u.email, name=u.name, role=m.role) for m, u in rows]


@router.delete("/{product_key}/members/{user_id}", status_code=204)
async def remove_member(user_id: str, ctx: tuple = Depends(require_product_admin),
                        db: AsyncSession = Depends(get_session)):
    product, actor, _ = ctx
    m = (await db.execute(select(Membership).where(
        Membership.user_id == user_id, Membership.product_id == product.id))).scalars().first()
    if m:
        await db.delete(m)
        await db.commit()
        await audit(db, actor, "member.remove", user_id, product.id)

# ---- audiences ----

@router.post("/{product_key}/audiences", response_model=AudienceOut, status_code=201)
async def add_audience(body: AudienceIn, ctx: tuple = Depends(require_product_admin),
                       db: AsyncSession = Depends(get_session)):
    product, actor, _ = ctx
    if body.key != "internal":
        raise HTTPException(422, "Only the 'internal' audience can be added — "
                                 "'external' is always available and is the default.")
    body.is_default = False                        # external stays the default
    exists = (await db.execute(select(Audience).where(
        Audience.product_id == product.id, Audience.key == body.key))).scalars().first()
    if exists:
        raise HTTPException(409, "Audience already exists")
    if body.is_default:
        for a in (await db.execute(select(Audience).where(
                Audience.product_id == product.id))).scalars().all():
            a.is_default = False
    aud = Audience(product_id=product.id, key=body.key,
                   display_name=body.display_name or body.key.title(), is_default=body.is_default)
    db.add(aud)
    await db.commit()
    await audit(db, actor, "audience.create", body.key, product.id)
    return aud


@router.delete("/{product_key}/audiences/{aud_key}", status_code=204)
async def delete_audience(aud_key: str, ctx: tuple = Depends(require_product_admin),
                          db: AsyncSession = Depends(get_session)):
    """Deleting an audience auto-strips its overlays from every tool (each strip is a
    new version, auditable), re-resolves views, and tells SDKs to reload."""
    from .. import overlay as ov
    from ..models import Entity, EntityVersion
    from ..services import audience_keys, next_seq
    product, actor, _ = ctx
    aud = (await db.execute(select(Audience).where(
        Audience.product_id == product.id, Audience.key == aud_key))).scalars().first()
    if not aud:
        raise HTTPException(404, "Audience not found")
    if aud.is_default:
        raise HTTPException(400, "Cannot delete the default audience")
    await db.delete(aud)
    await db.flush()
    remaining = await audience_keys(db, product.id)
    entities = (await db.execute(select(Entity).where(
        Entity.product_id == product.id, Entity.is_deleted == False))).scalars().all()  # noqa: E712
    stripped = []
    for e in entities:
        payload = dict(e.payload)
        if aud_key in (payload.get("audiences") or {}):
            payload = {**payload, "audiences": {k: v for k, v in payload["audiences"].items()
                                                if k != aud_key}}
            e.payload = payload
            e.version += 1
            db.add(EntityVersion(entity_id=e.id, version=e.version, payload=payload,
                                 author_id=actor.id, note=f"audience '{aud_key}' deleted"))
            stripped.append(e.name)
        e.resolved = ov.resolve_all(e.type, payload, remaining)
    await next_seq(db, product)
    await db.commit()
    await audit(db, actor, "audience.delete", aud_key, product.id, {"stripped_from": stripped})
    await publish_change(db, product, "manifest.reload", {})


@router.get("/{product_key}/audiences", response_model=list[AudienceOut])
async def list_audiences(ctx: tuple = Depends(require_member), db: AsyncSession = Depends(get_session)):
    product, _, _ = ctx
    return (await db.execute(select(Audience).where(Audience.product_id == product.id))).scalars().all()

# ---- API keys (data-plane credentials) ----

@router.post("/{product_key}/api-keys", response_model=ApiKeyCreated, status_code=201)
async def create_key(ctx: tuple = Depends(require_product_admin),
                     db: AsyncSession = Depends(get_session)):
    """ONE active key per product. Creating a key REVOKES any existing active key
    in the same transaction (rotation, not accumulation) — the plaintext is
    returned exactly once, for the team to keep in their MCP server's env."""
    product, actor, _ = ctx
    actives = (await db.execute(select(ApiKey).where(
        ApiKey.product_id == product.id, ApiKey.revoked == False))).scalars().all()  # noqa: E712
    for k in actives:
        k.revoked = True
    plaintext, digest, prefix = new_api_key()
    key = ApiKey(product_id=product.id, name="default", key_hash=digest, prefix=prefix,
                 secret_enc=encrypt_json({"key": plaintext}))
    db.add(key)
    await db.commit()
    await audit(db, actor, "apikey.rotate" if actives else "apikey.create",
                prefix, product.id, {"revoked": [k.prefix for k in actives]})
    return ApiKeyCreated(id=key.id, name=key.name, prefix=prefix, revoked=False, plaintext=plaintext)


@router.get("/{product_key}/api-keys/reveal")
async def reveal_key(ctx: tuple = Depends(require_product_admin),
                     db: AsyncSession = Depends(get_session)):
    """Product admins can copy the ACTIVE key any time (stored encrypted at rest)."""
    product, actor, _ = ctx
    key = (await db.execute(select(ApiKey).where(
        ApiKey.product_id == product.id, ApiKey.revoked == False))).scalars().first()  # noqa: E712
    if not key or not key.secret_enc:
        raise HTTPException(404, "No copyable active key — regenerate to create one")
    await audit(db, actor, "apikey.reveal", key.prefix, product.id)
    return {"plaintext": decrypt_json(key.secret_enc).get("key", ""), "prefix": key.prefix}


@router.get("/{product_key}/api-keys", response_model=list[ApiKeyOut])
async def list_keys(ctx: tuple = Depends(require_product_admin), db: AsyncSession = Depends(get_session)):
    product, _, _ = ctx
    return (await db.execute(select(ApiKey).where(ApiKey.product_id == product.id))).scalars().all()


@router.delete("/{product_key}/api-keys/{key_id}", status_code=204)
async def revoke_key(key_id: str, ctx: tuple = Depends(require_product_admin),
                     db: AsyncSession = Depends(get_session)):
    product, actor, _ = ctx
    key = await db.get(ApiKey, key_id)
    if key and key.product_id == product.id:
        key.revoked = True
        await db.commit()
        await audit(db, actor, "apikey.revoke", key.prefix, product.id)
