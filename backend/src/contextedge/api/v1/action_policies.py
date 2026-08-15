"""CRUD for action policies (F3b).

``action_policies`` shipped in ``0029`` with no way to put a row in it. A
policy table nobody can author is a vocabulary, not a control, so the engine
and this surface land together — an evaluator with an empty table would have
been the same gap wearing different clothes.

Version semantics match ``tenant_policies`` (F3): the version tracks the
**rules**, so renaming a policy or deactivating it does not bump it, and
changing a verdict, a scope axis or the precedence settings does. Every
``policy_checks`` row keys on the version it evaluated, and rewriting history
by editing a policy is exactly what versioning is for preventing.
"""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from contextedge.deps import AuthUser, DbSession
from contextedge.models.action_policy import ActionPolicy
from contextedge.services.action_policy_service import (
    ActionPolicyError,
    validate_policy_fields,
)

router = APIRouter()

# Fields whose change alters what the policy DECIDES. Editing any of these
# bumps the version; editing anything else does not.
_RULE_FIELDS = (
    "policy_result",
    "risk_level",
    "allowed_execution_mode",
    "required_approver_roles",
    "conditions",
    "restrictions",
    "priority",
    "policy_scope",
    "conflict_resolution",
    "workflow_entity_id",
    "environment",
    "business_unit",
    "data_domain",
    "effective_from",
    "effective_to",
)


class ActionPolicyResponse(BaseModel):
    id: UUID
    policy_name: str
    action_name: str
    workflow_entity_id: UUID | None
    environment: str | None
    business_unit: str | None
    data_domain: str | None
    risk_level: str
    policy_result: str
    required_approver_roles: list
    allowed_execution_mode: str | None
    conditions: dict
    restrictions: dict
    priority: int
    policy_scope: str | None
    conflict_resolution: str
    description: str | None
    is_active: bool
    version: int
    effective_from: datetime | None
    effective_to: datetime | None

    model_config = {"from_attributes": True}


class ActionPolicyCreate(BaseModel):
    policy_name: str = Field(..., min_length=1, max_length=255)
    action_name: str = Field(..., min_length=1, max_length=120)
    policy_result: str
    risk_level: str = "medium"
    workflow_entity_id: UUID | None = None
    environment: str | None = None
    business_unit: str | None = None
    data_domain: str | None = None
    required_approver_roles: list[str] = Field(default_factory=list)
    allowed_execution_mode: str | None = None
    conditions: dict = Field(default_factory=dict)
    restrictions: dict = Field(default_factory=dict)
    priority: int = 100
    policy_scope: str | None = None
    conflict_resolution: str = "most_restrictive"
    description: str | None = None
    is_active: bool = True
    effective_from: datetime | None = None
    effective_to: datetime | None = None


class ActionPolicyUpdate(BaseModel):
    policy_name: str | None = None
    policy_result: str | None = None
    risk_level: str | None = None
    workflow_entity_id: UUID | None = None
    environment: str | None = None
    business_unit: str | None = None
    data_domain: str | None = None
    required_approver_roles: list[str] | None = None
    allowed_execution_mode: str | None = None
    conditions: dict | None = None
    restrictions: dict | None = None
    priority: int | None = None
    policy_scope: str | None = None
    conflict_resolution: str | None = None
    description: str | None = None
    is_active: bool | None = None
    effective_from: datetime | None = None
    effective_to: datetime | None = None


@router.get("", response_model=list[ActionPolicyResponse])
async def list_action_policies(
    db: DbSession, user: AuthUser, action_name: str | None = None
):
    query = select(ActionPolicy).where(ActionPolicy.tenant_id == user.tenant_id)
    if action_name:
        query = query.where(ActionPolicy.action_name == action_name)
    result = await db.execute(query.order_by(ActionPolicy.action_name, ActionPolicy.priority))
    return list(result.scalars().all())


@router.post("", response_model=ActionPolicyResponse, status_code=status.HTTP_201_CREATED)
async def create_action_policy(body: ActionPolicyCreate, db: DbSession, user: AuthUser):
    user.require_role("tenant_admin")
    try:
        validate_policy_fields(
            policy_result=body.policy_result,
            conflict_resolution=body.conflict_resolution,
            risk_level=body.risk_level,
        )
    except ActionPolicyError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    row = ActionPolicy(tenant_id=user.tenant_id, **body.model_dump())
    db.add(row)
    await db.flush()
    await db.refresh(row)
    return row


async def _get(db: DbSession, user: AuthUser, policy_id: UUID) -> ActionPolicy:
    result = await db.execute(
        select(ActionPolicy).where(
            ActionPolicy.id == policy_id, ActionPolicy.tenant_id == user.tenant_id
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Action policy not found"
        )
    return row


@router.patch("/{policy_id}", response_model=ActionPolicyResponse)
async def update_action_policy(
    policy_id: UUID, body: ActionPolicyUpdate, db: DbSession, user: AuthUser
):
    user.require_role("tenant_admin")
    row = await _get(db, user, policy_id)

    changes = body.model_dump(exclude_unset=True)
    try:
        validate_policy_fields(
            policy_result=changes.get("policy_result", row.policy_result),
            conflict_resolution=changes.get("conflict_resolution", row.conflict_resolution),
            risk_level=changes.get("risk_level", row.risk_level),
        )
    except ActionPolicyError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    # The version tracks the RULES, not the labels. A rename does not change
    # what a past execution was judged under; a verdict or scope change does.
    rules_changed = any(
        field in changes and changes[field] != getattr(row, field)
        for field in _RULE_FIELDS
    )
    for field, value in changes.items():
        setattr(row, field, value)
    if rules_changed:
        row.version = (row.version or 1) + 1

    await db.flush()
    await db.refresh(row)
    return row


@router.delete("/{policy_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_action_policy(policy_id: UUID, db: DbSession, user: AuthUser):
    user.require_role("tenant_admin")
    row = await _get(db, user, policy_id)
    await db.delete(row)
    await db.flush()
