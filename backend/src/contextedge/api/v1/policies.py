from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from contextedge.deps import AuthUser, DbSession
from contextedge.models.policy import POLICY_TYPES, TenantPolicy

router = APIRouter()


class PolicyRecordResponse(BaseModel):
    id: UUID
    name: str
    description: str | None
    config: dict
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class PoliciesGroupedResponse(BaseModel):
    retention_policies: list[PolicyRecordResponse]
    classification_policies: list[PolicyRecordResponse]
    access_policies: list[PolicyRecordResponse]
    approval_policies: list[PolicyRecordResponse]


class PolicyCreate(BaseModel):
    policy_type: str = Field(..., description="retention|classification|access|approval")
    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    config: dict = Field(default_factory=dict)
    is_active: bool = True


class PolicyUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = None
    config: dict | None = None
    is_active: bool | None = None


def _empty_grouped() -> dict[str, list[PolicyRecordResponse]]:
    return {
        "retention_policies": [],
        "classification_policies": [],
        "access_policies": [],
        "approval_policies": [],
    }


@router.get("", response_model=PoliciesGroupedResponse)
async def list_policies(db: DbSession, user: AuthUser):
    if not (
        user.has_role("tenant_admin")
        or user.has_role("domain_admin")
        or user.has_role("knowledge_manager")
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tenant admin, domain admin, or knowledge manager role required",
        )
    result = await db.execute(
        select(TenantPolicy)
        .where(TenantPolicy.tenant_id == user.tenant_id)
        .order_by(TenantPolicy.policy_type, TenantPolicy.name)
    )
    rows = result.scalars().all()
    out = _empty_grouped()
    for p in rows:
        key = TenantPolicy.TYPE_TO_RESPONSE_KEY.get(p.policy_type)
        if key is None:
            continue
        out[key].append(PolicyRecordResponse.model_validate(p))
    return PoliciesGroupedResponse(**out)


@router.post("", response_model=PolicyRecordResponse, status_code=status.HTTP_201_CREATED)
async def create_policy(body: PolicyCreate, db: DbSession, user: AuthUser):
    user.require_role("tenant_admin")
    if body.policy_type not in POLICY_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"policy_type must be one of: {', '.join(sorted(POLICY_TYPES))}",
        )
    row = TenantPolicy(
        tenant_id=user.tenant_id,
        policy_type=body.policy_type,
        name=body.name.strip(),
        description=body.description.strip() if body.description else None,
        config=body.config or {},
        is_active=body.is_active,
    )
    db.add(row)
    await db.flush()
    await db.refresh(row)
    return row


async def _get_policy(
    db: DbSession, user: AuthUser, policy_id: UUID
) -> TenantPolicy:
    result = await db.execute(
        select(TenantPolicy).where(
            TenantPolicy.id == policy_id,
            TenantPolicy.tenant_id == user.tenant_id,
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Policy not found")
    return row


@router.patch("/{policy_id}", response_model=PolicyRecordResponse)
async def update_policy(
    policy_id: UUID,
    body: PolicyUpdate,
    db: DbSession,
    user: AuthUser,
):
    user.require_role("tenant_admin")
    row = await _get_policy(db, user, policy_id)
    if body.name is not None:
        row.name = body.name.strip()
    if body.description is not None:
        row.description = body.description.strip() or None
    if body.config is not None:
        row.config = body.config
    if body.is_active is not None:
        row.is_active = body.is_active
    await db.flush()
    await db.refresh(row)
    return row


@router.delete("/{policy_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_policy(policy_id: UUID, db: DbSession, user: AuthUser):
    user.require_role("tenant_admin")
    row = await _get_policy(db, user, policy_id)
    await db.delete(row)
    await db.flush()
