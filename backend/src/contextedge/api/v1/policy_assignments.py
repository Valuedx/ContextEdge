from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from contextedge.deps import AuthUser, DbSession
from contextedge.models.evidence import EvidenceItem
from contextedge.models.playbook import Playbook
from contextedge.models.source import Source
from contextedge.schemas.review import (
    PolicyAssignmentRequest,
    PolicyAssignmentResponse,
)
from contextedge.services.policy_assignment import assert_policy_assignment

router = APIRouter()


def _assignment_response(resource_type: str, resource_id: UUID, policy_type: str, policy_id: UUID) -> PolicyAssignmentResponse:
    return PolicyAssignmentResponse(
        resource_type=resource_type,
        resource_id=resource_id,
        policy_type=policy_type,
        policy_id=policy_id,
    )


async def _load_assignment_target(
    db: DbSession,
    *,
    tenant_id: UUID,
    resource_type: str,
    resource_id: UUID,
):
    model = {"evidence": EvidenceItem, "source": Source, "playbook": Playbook}.get(resource_type)
    if model is None:
        raise HTTPException(status_code=400, detail="Unsupported resource_type")
    target = (
        await db.execute(
            select(model).where(model.id == resource_id, model.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=404, detail=f"{resource_type.title()} not found")
    return target


def _resource_policy_field(resource_type: str, policy_type: str) -> str:
    mapping = {
        ("evidence", "access"): "access_policy_id",
        ("source", "classification"): "classification_policy_id",
        ("source", "retention"): "retention_policy_id",
        ("playbook", "approval"): "approval_policy_id",
    }
    field = mapping.get((resource_type, policy_type))
    if field is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported policy assignment for resource type",
        )
    return field


@router.get("", response_model=list[PolicyAssignmentResponse])
async def list_policy_assignments(
    db: DbSession,
    user: AuthUser,
    resource_type: str | None = Query(None, pattern=r"^(evidence|source|playbook)$"),
    resource_id: UUID | None = None,
):
    user.require_role("domain_admin")
    assignments: list[PolicyAssignmentResponse] = []

    if resource_type in (None, "evidence"):
        stmt = select(EvidenceItem).where(
            EvidenceItem.tenant_id == user.tenant_id,
            EvidenceItem.access_policy_id.is_not(None),
        )
        if resource_id and resource_type == "evidence":
            stmt = stmt.where(EvidenceItem.id == resource_id)
        for row in (await db.execute(stmt)).scalars().all():
            assignments.append(_assignment_response("evidence", row.id, "access", row.access_policy_id))

    if resource_type in (None, "source"):
        stmt = select(Source).where(Source.tenant_id == user.tenant_id)
        if resource_id and resource_type == "source":
            stmt = stmt.where(Source.id == resource_id)
        for row in (await db.execute(stmt)).scalars().all():
            if row.classification_policy_id:
                assignments.append(_assignment_response("source", row.id, "classification", row.classification_policy_id))
            if row.retention_policy_id:
                assignments.append(_assignment_response("source", row.id, "retention", row.retention_policy_id))

    if resource_type in (None, "playbook"):
        stmt = select(Playbook).where(
            Playbook.tenant_id == user.tenant_id,
            Playbook.approval_policy_id.is_not(None),
        )
        if resource_id and resource_type == "playbook":
            stmt = stmt.where(Playbook.id == resource_id)
        for row in (await db.execute(stmt)).scalars().all():
            assignments.append(_assignment_response("playbook", row.id, "approval", row.approval_policy_id))

    return assignments


@router.post("", response_model=PolicyAssignmentResponse, status_code=status.HTTP_201_CREATED)
async def assign_policy(
    body: PolicyAssignmentRequest,
    db: DbSession,
    user: AuthUser,
):
    user.require_role("domain_admin")
    await assert_policy_assignment(db, user.tenant_id, body.policy_id, body.policy_type)
    target = await _load_assignment_target(
        db,
        tenant_id=user.tenant_id,
        resource_type=body.resource_type,
        resource_id=body.resource_id,
    )
    field = _resource_policy_field(body.resource_type, body.policy_type)
    setattr(target, field, body.policy_id)
    await db.flush()
    return _assignment_response(body.resource_type, body.resource_id, body.policy_type, body.policy_id)


@router.delete("", response_model=PolicyAssignmentResponse)
async def delete_policy_assignment(
    db: DbSession,
    user: AuthUser,
    resource_type: str = Query(..., pattern=r"^(evidence|source|playbook)$"),
    resource_id: UUID = Query(...),
    policy_type: str = Query(..., pattern=r"^(access|classification|retention|approval)$"),
):
    user.require_role("domain_admin")
    target = await _load_assignment_target(
        db,
        tenant_id=user.tenant_id,
        resource_type=resource_type,
        resource_id=resource_id,
    )
    field = _resource_policy_field(resource_type, policy_type)
    current = getattr(target, field)
    if current is None:
        raise HTTPException(status_code=404, detail="Policy assignment not found")
    setattr(target, field, None)
    await db.flush()
    return _assignment_response(resource_type, resource_id, policy_type, current)
