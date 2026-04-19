from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from contextedge.models.decision import REJECTION_REASON_CODES


class DecisionOptionCreate(BaseModel):
    action: str
    suitability: float | None = None
    risk_level: str | None = None
    preconditions: list[str] = Field(default_factory=list)
    rejection_reason: str | None = None
    rejection_code: str | None = None
    selected: bool = False

    @field_validator("rejection_code")
    @classmethod
    def _validate_rejection_code(cls, v: str | None) -> str | None:
        if v is not None and v not in REJECTION_REASON_CODES:
            raise ValueError(
                f"rejection_code must be one of {REJECTION_REASON_CODES}",
            )
        return v


class DecisionOptionResponse(BaseModel):
    id: UUID
    decision_id: UUID
    tenant_id: UUID
    action: str
    suitability: float | None
    risk_level: str | None
    preconditions: list
    rejection_reason: str | None
    rejection_code: str | None
    selected: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class DecisionOutcomeCreate(BaseModel):
    action_executed: str
    execution_result: str = Field(
        ..., description="success, failure, partial, timeout, or rejected",
    )
    result_details: dict = Field(default_factory=dict)
    follow_up_needed: bool = False
    follow_up_decision_id: UUID | None = None
    feedback_received: str | None = None
    feedback_code: str | None = None
    feedback_by: UUID | None = None

    @field_validator("feedback_code")
    @classmethod
    def _validate_feedback_code(cls, v: str | None) -> str | None:
        if v is not None and v not in REJECTION_REASON_CODES:
            raise ValueError(
                f"feedback_code must be one of {REJECTION_REASON_CODES}",
            )
        return v


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
    feedback_code: str | None
    feedback_by: UUID | None
    created_at: datetime

    model_config = {"from_attributes": True}


class DecisionRejectRequest(BaseModel):
    """Structured rejection of an AI-recommended decision.

    `code` feeds the learning loop; `comment` captures free-text nuance
    (required when code is "other").
    """

    code: str = Field(
        ...,
        description=f"One of {REJECTION_REASON_CODES}",
    )
    comment: str | None = None

    @field_validator("code")
    @classmethod
    def _validate_code(cls, v: str) -> str:
        if v not in REJECTION_REASON_CODES:
            raise ValueError(
                f"code must be one of {REJECTION_REASON_CODES}",
            )
        return v


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


class ProvenanceEvidenceItem(BaseModel):
    """One evidence node cited as `based_on` by a decision.

    Everything the reviewer console's Zone 4/5 drill-in card needs — title
    + summary + source provenance + an optional deep link back to the
    origin system (ServiceNow ticket, Jira issue, Gmail thread, etc.)."""

    evidence_id: UUID
    title: str | None
    body_summary: str | None
    evidence_type: str
    source_id: UUID
    source_type: str
    source_display_name: str
    external_id: str | None
    deep_link: str | None
    delta_signal: str | None
    ingested_at: datetime


class ProvenanceEpisodeItem(BaseModel):
    episode_id: UUID
    title: str
    status: str
    final_outcome: str | None
    extraction_confidence: float


class ProvenancePatternItem(BaseModel):
    pattern_id: UUID
    title: str
    pattern_type: str
    confidence: float
    episode_count: int


class SimilarDecisionsAggregateResponse(BaseModel):
    """One-call bundle for Zone 5 similar-decisions provenance.

    Combines the top-N similar decisions (as `find_similar_decisions`
    returns them) with a total count and outcome aggregate so the UI can
    render "based on 143 similar tickets, 87% succeeded" plus the top
    few examples in a single round trip — no client-side fan-out.

    `success_rate` is computed from `outcomes` — it's `success / sum(counted)`
    where `counted` is `success|failure|partial|timeout|rejected`. Unknown
    outcome labels are ignored so they can't skew the denominator.
    `success_rate` is null when no outcomes are recorded.
    """

    decision_type: str
    context_filters: dict = Field(default_factory=dict)
    total_count: int
    outcomes: dict[str, int] = Field(default_factory=dict)
    success_rate: float | None = None
    decisions: list[DecisionResponse] = Field(default_factory=list)


class DecisionProvenanceResponse(BaseModel):
    """Full provenance for one Decision — hydrated `based_on` references
    grouped by target node type. Powers Zone 5's "based on these evidence
    items / episodes / patterns" drill-in and the deep-link back to the
    origin system for each evidence citation."""

    decision_id: UUID
    evidence: list[ProvenanceEvidenceItem] = Field(default_factory=list)
    episodes: list[ProvenanceEpisodeItem] = Field(default_factory=list)
    patterns: list[ProvenancePatternItem] = Field(default_factory=list)
