from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class DecisionOptionCreate(BaseModel):
    action: str
    suitability: float | None = None
    risk_level: str | None = None
    preconditions: list[str] = Field(default_factory=list)
    rejection_reason: str | None = None
    selected: bool = False


class DecisionOptionResponse(BaseModel):
    id: UUID
    decision_id: UUID
    tenant_id: UUID
    action: str
    suitability: float | None
    risk_level: str | None
    preconditions: list
    rejection_reason: str | None
    selected: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class DecisionOutcomeCreate(BaseModel):
    action_executed: str
    execution_result: str = Field(
        ..., description="success, failure, partial, or timeout",
    )
    result_details: dict = Field(default_factory=dict)
    follow_up_needed: bool = False
    follow_up_decision_id: UUID | None = None
    feedback_received: str | None = None
    feedback_by: UUID | None = None


class DecisionOutcomeResponse(BaseModel):
    id: UUID
    decision_id: UUID
    tenant_id: UUID
    action_executed: str
    execution_result: str
    result_details: dict
    follow_up_needed: bool
    follow_up_decision_id: UUID | None
    feedback_received: str | None
    feedback_by: UUID | None
    created_at: datetime

    model_config = {"from_attributes": True}


class EvidenceRef(BaseModel):
    ref_type: str
    ref_id: str
    description: str = ""


class DecisionCreate(BaseModel):
    session_id: UUID | None = None
    domain_id: UUID | None = None
    parent_decision_id: UUID | None = None
    decision_type: str
    agent_step: str
    actor_type: str = "ai"
    actor_id: UUID | None = None
    context_snapshot: dict = Field(default_factory=dict)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    options: list[DecisionOptionCreate] = Field(default_factory=list)
    rationale_summary: str
    confidence: float | None = None
    uncertainty_notes: str | None = None
    compact_trace: str | None = None
    explanation: str | None = None
    approval_required: bool = False
    policy_refs: list[str] = Field(default_factory=list)
    human_override: bool = False
    status: str = "pending"


class DecisionResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    domain_id: UUID | None
    session_id: UUID | None
    parent_decision_id: UUID | None
    decision_type: str
    agent_step: str
    actor_type: str
    actor_id: UUID | None
    context_snapshot: dict
    evidence_summary: list
    rationale_summary: str
    confidence: float | None
    uncertainty_notes: str | None
    compact_trace: str | None
    explanation: str | None
    approval_required: bool
    policy_refs: list
    human_override: bool
    status: str
    created_at: datetime
    updated_at: datetime
    options: list[DecisionOptionResponse] = Field(default_factory=list)
    outcomes: list[DecisionOutcomeResponse] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class DecisionChainResponse(BaseModel):
    decisions: list[DecisionResponse]
