from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from contextedge.deps import AuthUser, DbSession
from contextedge.models.episode import CanonicalIdentity, IdentityAlias
from contextedge.schemas.review import (
    IdentityMergeRequest,
    IdentityResponse,
    IdentityUpdate,
)
from contextedge.services.identity_service import merge_canonical_identities

router = APIRouter()


@router.get("", response_model=list[IdentityResponse])
async def list_identities(
    db: DbSession,
    user: AuthUser,
    entity_type: str | None = None,
    active_only: bool = True,
    query: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    user.require_role("knowledge_manager")
    stmt = (
        select(CanonicalIdentity)
        .where(CanonicalIdentity.tenant_id == user.tenant_id)
        .options(selectinload(CanonicalIdentity.aliases))
        .order_by(CanonicalIdentity.canonical_name.asc())
        .limit(limit)
        .offset(offset)
    )
    if entity_type is not None:
        stmt = stmt.where(CanonicalIdentity.entity_type == entity_type)
    if active_only:
        stmt = stmt.where(CanonicalIdentity.is_active.is_(True))
    if query:
        stmt = stmt.where(CanonicalIdentity.canonical_name.ilike(f"%{query}%"))
    result = await db.execute(stmt)
    return result.scalars().all()


@router.patch("/{identity_id}", response_model=IdentityResponse)
async def update_identity(
    identity_id: UUID,
    body: IdentityUpdate,
    db: DbSession,
    user: AuthUser,
):
    user.require_role("knowledge_manager")
    identity = (
        await db.execute(
            select(CanonicalIdentity)
            .where(
                CanonicalIdentity.id == identity_id,
                CanonicalIdentity.tenant_id == user.tenant_id,
            )
            .options(selectinload(CanonicalIdentity.aliases))
        )
    ).scalar_one_or_none()
    if not identity:
        raise HTTPException(status_code=404, detail="Identity not found")

    update_data = body.model_dump(exclude_unset=True, exclude={"add_aliases"})
    for field, value in update_data.items():
        setattr(identity, field, value)

    existing_aliases = {identity.canonical_name.casefold(), *(alias.alias_text.casefold() for alias in identity.aliases)}
    for alias_text in body.add_aliases:
        normalized = alias_text.strip().casefold()
        if not normalized or normalized in existing_aliases:
            continue
        db.add(
            IdentityAlias(
                canonical_identity_id=identity.id,
                alias_text=alias_text.strip(),
                source_id=None,
                confidence=1.0,
                created_by=user.email,
            )
        )
        existing_aliases.add(normalized)

    await db.flush()
    result = await db.execute(
        select(CanonicalIdentity)
        .where(CanonicalIdentity.id == identity.id)
        .options(selectinload(CanonicalIdentity.aliases))
    )
    return result.scalar_one()


@router.post("/merge", response_model=IdentityResponse)
async def merge_identities(
    body: IdentityMergeRequest,
    db: DbSession,
    user: AuthUser,
):
    user.require_role("knowledge_manager")
    identity = await merge_canonical_identities(
        db,
        tenant_id=user.tenant_id,
        primary_identity_id=body.primary_identity_id,
        duplicate_identity_id=body.duplicate_identity_id,
        actor_id=user.user_id,
    )
    if identity is None:
        raise HTTPException(status_code=404, detail="One or both identities were not found")
    result = await db.execute(
        select(CanonicalIdentity)
        .where(CanonicalIdentity.id == identity.id)
        .options(selectinload(CanonicalIdentity.aliases))
    )
    return result.scalar_one()
