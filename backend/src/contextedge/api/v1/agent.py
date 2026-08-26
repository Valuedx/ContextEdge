"""Agent host surface. Nothing in SupportCopilot consumes this yet."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from contextedge.config import settings
from contextedge.deps import AuthUser, DbSession
from contextedge.integrations.maf.runtime import (
    TenantBoundSessionFactory,
    _playbook_client_for,
    run_diagnose,
)

router = APIRouter()


class DiagnoseRequest(BaseModel):
    symptoms: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)
    environment: dict = Field(default_factory=dict)
    context: str | None = None
    session_id: UUID | None = None
    domain_id: UUID | None = None
    top_k: int = Field(5, ge=1, le=20)


class DiagnoseResponse(BaseModel):
    playbook_id: UUID | None = None
    playbook_version_id: UUID | None = None
    semantic_version: str | None = None
    stable_key: str | None = None
    applicability: str | None = None
    applicability_factors: list[str] | None = None
    applicability_differences: list[str] | None = None
    selection_margin: float | None = None
    confidence_calibrated: float | None = None
    cited_node_keys: list[str] = Field(default_factory=list)
    grounding_status: str
    rationale: str
    warnings: list[str] = Field(default_factory=list)
    truncation_reasons: list[str] = Field(default_factory=list)
    candidates: list[dict] = Field(default_factory=list)
    tool_calls: list[dict] = Field(default_factory=list)
    agent_mode: str = "tools"


class TriggerCheckRequest(BaseModel):
    playbook_version_id: UUID
    environment: dict = Field(default_factory=dict)
    symptoms: list[str] = Field(default_factory=list)


@router.post("/diagnose", response_model=DiagnoseResponse)
async def diagnose(body: DiagnoseRequest, db: DbSession, user: AuthUser):
    if not settings.agent_diagnose_enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent diagnose is disabled",
        )
    payload = await run_diagnose(
        db,
        user,
        symptoms=body.symptoms,
        entities=body.entities,
        environment=body.environment,
        context=body.context,
        domain_id=body.domain_id,
        session_id=body.session_id,
        top_k=body.top_k,
        session_factory=TenantBoundSessionFactory(user.tenant_id),
    )
    return DiagnoseResponse.model_validate(payload)


@router.post("/trigger-check")
async def trigger_check(body: TriggerCheckRequest, db: DbSession, user: AuthUser):
    """HTTP port for ``check_trigger_conditions`` (HttpPlaybookRetrievalClient)."""
    client = _playbook_client_for(db, user, domain_id=None)
    return await client.check_trigger_conditions(
        body.playbook_version_id,
        body.environment,
        body.symptoms,
    )


@router.get("/negative-knowledge/{playbook_version_id}")
async def agent_negative_knowledge(
    playbook_version_id: UUID, db: DbSession, user: AuthUser
):
    """HTTP port for ``get_negative_knowledge`` (HttpPlaybookRetrievalClient)."""
    client = _playbook_client_for(db, user, domain_id=None)
    return await client.get_negative_knowledge(playbook_version_id)
