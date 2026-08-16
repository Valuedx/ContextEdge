"""Authoring surface for the skill registry (F6).

F6 shipped the registry — `Skill` (what can be invoked) and
`ExecutionContract` (the envelope it must be invoked inside) — with
validation that refuses a destructive skill without a replay guarantee, and
a resolver that turns a playbook step's `tool_ref` into a real definition.

Nothing has ever put a row in it. So `tool_ref` resolves to nothing, and an
approved playbook has no way to say what to actually call. A registry nobody
can author is a vocabulary, not a control — the same sentence F3b's action
policies earned, for the same reason.

Two lifecycle rules, both borrowed from surfaces that already work here:

- **A skill is born `draft`.** `status` is not accepted on create. Something
  registered as immediately invocable skips the moment a human looks at what
  it can do and at what safety class — which is the entire point of having a
  registry rather than a string.
- **Rules get a new version; labels get an edit.** An active skill's
  endpoint, safety class, interface or contract cannot be mutated: a playbook
  was approved against those, and changing them under it rewrites what a past
  approval meant. Name and description are labels and may be corrected in
  place. Same distinction `action_policies` versions on.
"""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from contextedge.deps import AuthUser, DbSession
from contextedge.models.execution import ACTION_TYPES, SAFETY_CLASSES
from contextedge.models.skill import (
    IDEMPOTENCY_MODES,
    INTERFACE_TYPES,
    RETRY_BACKOFFS,
    SKILL_STATUSES,
    ExecutionContract,
    Skill,
)
from contextedge.services.skill_registry_service import (
    SkillRegistryError,
    register_execution_contract,
    register_skill,
)

router = APIRouter()

# What a status may become. Deprecation is reversible — a skill withdrawn in
# haste can come back — but retirement is not: bringing back something a human
# retired should cost a new version, so the decision stays legible.
_ALLOWED_TRANSITIONS: dict[str, tuple[str, ...]] = {
    "draft": ("active", "retired"),
    "active": ("deprecated", "retired"),
    "deprecated": ("active", "retired"),
    "retired": (),
}

# Fields that describe what the skill DOES. Changing one changes what a
# playbook approved against it will invoke, so they are version-scoped rather
# than editable. Everything else is a label.
_RULE_FIELDS = (
    "interface_type", "endpoint_or_tool", "safety_class", "action_type",
    "input_schema", "output_schema", "reversible", "rollback_skill_id",
    "allowed_principal_roles", "execution_contract_id",
)


class ExecutionContractCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    idempotency_mode: str = Field(..., description=f"One of {IDEMPOTENCY_MODES}")
    description: str | None = None
    deduplication_window_sec: int | None = Field(default=None, ge=1)
    timeout_sec: int = Field(60, ge=1, le=86_400)
    max_attempts: int = Field(1, ge=1, le=10)
    retry_backoff: str = Field("none", description=f"One of {RETRY_BACKOFFS}")
    supports_cancellation: bool = False
    supports_dry_run: bool = False
    concurrency_policy: str = "allow"
    max_concurrency: int | None = Field(default=None, ge=1)
    rate_limit_per_minute: int | None = Field(default=None, ge=1)
    credential_scope: str | None = Field(default=None, max_length=120)
    expected_duration_sec: int | None = Field(default=None, ge=1)


class ExecutionContractResponse(BaseModel):
    id: UUID
    name: str
    description: str | None
    idempotency_mode: str
    deduplication_window_sec: int | None
    timeout_sec: int
    max_attempts: int
    retry_backoff: str
    supports_cancellation: bool
    supports_dry_run: bool
    concurrency_policy: str
    max_concurrency: int | None
    rate_limit_per_minute: int | None
    credential_scope: str | None
    expected_duration_sec: int | None
    contract_version: int
    created_at: datetime

    model_config = {"from_attributes": True}


class SkillCreate(BaseModel):
    """No `status` field, deliberately — see the module docstring."""

    skill_key: str = Field(..., min_length=1, max_length=120)
    name: str = Field(..., min_length=1, max_length=255)
    interface_type: str = Field(..., description=f"One of {INTERFACE_TYPES}")
    safety_class: str = Field(..., description=f"One of {SAFETY_CLASSES}")
    version: str = Field("1.0.0", max_length=20)
    description: str | None = None
    action_type: str | None = Field(default=None, description=f"One of {ACTION_TYPES}")
    endpoint_or_tool: str | None = Field(default=None, max_length=500)
    input_schema: dict | None = None
    output_schema: dict | None = None
    reversible: bool = False
    rollback_skill_id: UUID | None = None
    allowed_principal_roles: list[str] = Field(default_factory=list)
    execution_contract_id: UUID | None = None


class SkillUpdate(BaseModel):
    """Labels only. A rule change is a new version."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None


class SkillStatusChange(BaseModel):
    status: str = Field(..., description=f"One of {SKILL_STATUSES}")


class SkillResponse(BaseModel):
    id: UUID
    skill_key: str
    version: str
    name: str
    description: str | None
    action_type: str | None
    interface_type: str
    endpoint_or_tool: str | None
    input_schema: dict | None
    output_schema: dict | None
    reversible: bool
    rollback_skill_id: UUID | None
    safety_class: str
    allowed_principal_roles: list
    execution_contract_id: UUID | None
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}


# -- execution contracts ------------------------------------------------


@router.get("/execution-contracts", response_model=list[ExecutionContractResponse])
async def list_execution_contracts(db: DbSession, user: AuthUser):
    rows = (
        await db.execute(
            select(ExecutionContract)
            .where(ExecutionContract.tenant_id == user.tenant_id)
            .order_by(ExecutionContract.name)
        )
    ).scalars().all()
    return list(rows)


@router.post(
    "/execution-contracts",
    response_model=ExecutionContractResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_execution_contract(
    body: ExecutionContractCreate, db: DbSession, user: AuthUser
):
    user.require_role("tenant_admin")
    try:
        contract = await register_execution_contract(
            db, tenant_id=user.tenant_id, **body.model_dump()
        )
    except SkillRegistryError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    await db.commit()
    return contract


# -- skills -------------------------------------------------------------


@router.get("", response_model=list[SkillResponse])
async def list_skills(
    db: DbSession,
    user: AuthUser,
    skill_key: str | None = None,
    skill_status: str | None = Query(None, alias="status"),
    action_type: str | None = None,
    limit: int = Query(100, ge=1, le=500),
):
    query = select(Skill).where(Skill.tenant_id == user.tenant_id)
    if skill_key:
        query = query.where(Skill.skill_key == skill_key)
    if skill_status:
        if skill_status not in SKILL_STATUSES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"status must be one of {list(SKILL_STATUSES)}",
            )
        query = query.where(Skill.status == skill_status)
    if action_type:
        query = query.where(Skill.action_type == action_type)
    rows = (
        await db.execute(query.order_by(Skill.skill_key, Skill.version).limit(limit))
    ).scalars().all()
    return list(rows)


@router.post("", response_model=SkillResponse, status_code=status.HTTP_201_CREATED)
async def create_skill(body: SkillCreate, db: DbSession, user: AuthUser):
    """Register a skill. It lands `draft` and is not invocable until activated."""
    user.require_role("tenant_admin")
    try:
        skill = await register_skill(
            db,
            tenant_id=user.tenant_id,
            created_by=user.user_id,
            **body.model_dump(),
        )
    except SkillRegistryError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    await db.commit()
    return skill


async def _get_skill(db, user, skill_id: UUID) -> Skill:
    skill = (
        await db.execute(
            select(Skill).where(Skill.id == skill_id, Skill.tenant_id == user.tenant_id)
        )
    ).scalar_one_or_none()
    if skill is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill not found")
    return skill


@router.get("/{skill_id}", response_model=SkillResponse)
async def get_skill(skill_id: UUID, db: DbSession, user: AuthUser):
    return await _get_skill(db, user, skill_id)


@router.patch("/{skill_id}", response_model=SkillResponse)
async def update_skill(skill_id: UUID, body: SkillUpdate, db: DbSession, user: AuthUser):
    """Correct a label. Anything that changes what the skill DOES needs a new
    version — a playbook was approved against the old definition."""
    user.require_role("tenant_admin")
    skill = await _get_skill(db, user, skill_id)
    changes = body.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(skill, field, value)
    await db.commit()
    await db.refresh(skill)
    return skill


@router.post("/{skill_id}/status", response_model=SkillResponse)
async def change_skill_status(
    skill_id: UUID, body: SkillStatusChange, db: DbSession, user: AuthUser
):
    """Move a skill through its lifecycle. Retirement is one-way."""
    user.require_role("tenant_admin")
    if body.status not in SKILL_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"status must be one of {list(SKILL_STATUSES)}",
        )
    skill = await _get_skill(db, user, skill_id)
    allowed = _ALLOWED_TRANSITIONS.get(skill.status, ())
    if body.status != skill.status and body.status not in allowed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"cannot move a {skill.status!r} skill to {body.status!r}; "
                f"allowed from here: {list(allowed) or 'none — register a new version'}"
            ),
        )
    skill.status = body.status
    await db.commit()
    await db.refresh(skill)
    return skill


# Rule fields are named here so the test that pins "a rule change needs a new
# version" reads the same list the docstring promises.
RULE_FIELDS = _RULE_FIELDS
