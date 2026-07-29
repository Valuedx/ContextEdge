from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from contextedge.deps import AuthUser, DbSession
from contextedge.models.episode import (
    RESOLUTION_STATES,
    CanonicalIdentity,
    IdentityAlias,
)
from contextedge.schemas.review import (
    IdentityMergeRequest,
    IdentityResponse,
    IdentityUpdate,
)
from contextedge.services.identity_normalizer import (
    _classify_bare_name,
    normalize_text,
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
    resolution_state: str | None = None,
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
    if resolution_state is not None:
        # The review queue: ?resolution_state=needs_review lists the
        # resolver's parked matches (hits ix_canonical_identities_resolution_state).
        if resolution_state not in RESOLUTION_STATES:
            raise HTTPException(
                status_code=400,
                detail=f"resolution_state must be one of {RESOLUTION_STATES}",
            )
        stmt = stmt.where(CanonicalIdentity.resolution_state == resolution_state)
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
    if "resolution_state" in update_data:
        new_state = update_data["resolution_state"]
        if new_state not in RESOLUTION_STATES:
            raise HTTPException(
                status_code=400,
                detail=f"resolution_state must be one of {RESOLUTION_STATES}",
            )
        # A human set it — record the method so the audit trail shows why.
        identity.resolution_method = "human_review"
    for field, value in update_data.items():
        setattr(identity, field, value)

    existing_aliases = {
        normalize_text(identity.canonical_name),
        *(normalize_text(alias.alias_text) for alias in identity.aliases),
    }
    for alias_text in body.add_aliases:
        cleaned = alias_text.strip()
        normalized = normalize_text(cleaned)
        if not normalized or normalized in existing_aliases:
            continue
        # Classify typed identifiers so a human-entered email/hostname
        # participates in Layer-1 strong matching, and populate the 0033
        # columns so the alias is visible to the typed lookup index.
        alias_type = _classify_bare_name(cleaned) or "display_name"
        if alias_type != "display_name":
            # Strong identifiers are unique per tenant
            # (uq_identity_aliases_tenant_strong) — surface an ownership
            # conflict as a 409 instead of a unique-violation 500.
            owner = (
                await db.execute(
                    select(IdentityAlias.canonical_identity_id).where(
                        IdentityAlias.tenant_id == identity.tenant_id,
                        IdentityAlias.alias_type == alias_type,
                        IdentityAlias.normalized_alias == normalized,
                    )
                )
            ).scalar_one_or_none()
            if owner is not None and owner != identity.id:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"{alias_type} '{cleaned}' already belongs to identity "
                        f"{owner}; merge the identities instead"
                    ),
                )
        db.add(
            IdentityAlias(
                canonical_identity_id=identity.id,
                tenant_id=identity.tenant_id,
                alias_text=cleaned,
                normalized_alias=normalized,
                alias_type=alias_type,
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
