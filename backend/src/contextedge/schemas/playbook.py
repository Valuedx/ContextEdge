from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class PatternResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    domain_id: UUID | None
    title: str
    description: str | None
    pattern_type: str
    confidence: float
    episode_count: int
    active_flag: bool
    contradiction_score: float
    freshness_score: float
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class PlaybookResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    domain_id: UUID | None
    stable_key: str
    title: str
    description: str | None
    lifecycle_state: str
    risk_tier: str
    automation_mode: str
    owner_user_id: UUID
    reviewer_user_id: UUID | None
    approver_user_id: UUID | None
    current_version_id: UUID | None
    last_validated_at: datetime | None
    expiry_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class PlaybookVersionResponse(BaseModel):
    id: UUID
    playbook_id: UUID
    semantic_version: str
    trigger_conditions: dict
    branching_logic: dict
    inputs: list | dict
    outputs: list | dict
    steps: list
    rollback_notes: str | None
    evidence_refs: list | None
    playbook_confidence: float
    execution_confidence_guidance: str | None
    published_at: datetime | None
    published_by: UUID | None
    created_at: datetime

    model_config = {"from_attributes": True}


class PlaybookCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    description: str | None = None
    domain_id: UUID | None = None
    risk_tier: str = "medium"
    automation_mode: str = "suggest_only"
    pattern_id: UUID | None = None


class PlaybookUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    risk_tier: str | None = None
    automation_mode: str | None = None
    reviewer_user_id: UUID | None = None


class PlaybookTransition(BaseModel):
    new_state: str
    comments: str | None = None


class PlaybookVersionCreate(BaseModel):
    semantic_version: str | None = Field(
        None,
        min_length=1,
        max_length=20,
        pattern=r"^\d+\.\d+\.\d+$",
    )
    trigger_conditions: dict = Field(default_factory=dict)
    branching_logic: dict = Field(default_factory=dict)
    inputs: list = Field(default_factory=list)
    outputs: list = Field(default_factory=list)
    steps: list = Field(default_factory=list)
    rollback_notes: str | None = None
    evidence_refs: list | None = None
    playbook_confidence: float = 0.5
    execution_confidence_guidance: str | None = None


class RuntimeMatchRequest(BaseModel):
    symptoms: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)
    environment: dict = Field(default_factory=dict)
    context: str | None = None
    top_k: int = Field(5, ge=1, le=20)
    domain_id: UUID | None = Field(
        None,
        description=(
            "Scope ranking to this domain; playbooks with no domain remain eligible (tenant-wide)."
        ),
    )


class RuntimeMatchResult(BaseModel):
    playbook_id: UUID
    playbook_title: str
    stable_key: str
    match_score: float
    confidence: float
    freshness_status: str
    evidence_count: int
    risk_tier: str
    automation_mode: str
    scoring_breakdown: dict | None = None


class RuntimeMatchResponse(BaseModel):
    match_id: str
    results: list[RuntimeMatchResult]
    fallback_guidance: str | None = None
    filters_applied: dict = Field(
        default_factory=dict,
        description="Effective retrieval filters (domain scope, risk cap, etc.)",
    )


class RuntimeExplainResponse(BaseModel):
    match_id: str
    query_text: str
    symptoms: list[str]
    entities: list[str]
    environment: dict
    ranked_results: list[dict]
    fallback_guidance: str | None = None
    filters_applied: dict = Field(default_factory=dict)


class FeedbackSubmission(BaseModel):
    match_id: str | None = None
    playbook_id: UUID | None = None
    feedback_type: str
    details: dict = Field(default_factory=dict)
