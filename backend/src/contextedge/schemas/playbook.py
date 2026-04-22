from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from contextedge.models.execution import SAFETY_CLASSES
from contextedge.models.playbook import AUTOMATION_MODES


def _validate_automation_mode(value: str) -> str:
    if value not in AUTOMATION_MODES:
        raise ValueError(
            f"automation_mode must be one of {AUTOMATION_MODES}, got {value!r}"
        )
    return value


class PlaybookStep(BaseModel):
    """One step in a playbook plan.

    All fields are optional with sensible defaults so existing JSONB step
    shapes (from pre-M2 data and AI-generated playbooks) keep validating.
    `extra = "allow"` preserves vendor-specific fields callers already
    store in the JSONB array.

    The fields the reviewer console Zone 6 renders are:
    - `reversible` → whether Undo is available for this step
    - `time_estimate_sec` → "est. 30 sec" badge
    - `verification` → marks a recheck/verification step vs an action step
    - `rollback_hint` → free-text Undo guidance
    - `safety_class` → per-step override of playbook-level safety
    - `tool_ref` → which connector/tool invokes this step (optional)
    """

    model_config = ConfigDict(extra="allow")

    index: int | None = None
    title: str | None = None
    description: str | None = None
    safety_class: str | None = None
    requires_approval: bool = False
    reversible: bool = False
    time_estimate_sec: int | None = Field(default=None, ge=0)
    verification: bool = False
    rollback_hint: str | None = None
    tool_ref: str | None = None
    inputs: dict = Field(default_factory=dict)
    outputs_schema: dict | None = None

    @field_validator("safety_class")
    @classmethod
    def _validate_safety_class(cls, v: str | None) -> str | None:
        if v is not None and v not in SAFETY_CLASSES:
            raise ValueError(f"safety_class must be one of {SAFETY_CLASSES}")
        return v


class VerificationPolicy(BaseModel):
    """Per-playbook verification policy — powers the reviewer UI's "re-check
    telemetry 30 min post-action, auto-close if resolved" commitment that
    makes engineers willing to approve rather than manually verify.

    All fields optional so a playbook can opt into verification incrementally.
    """

    auto_close_on_success: bool = False
    recheck_after_sec: int | None = Field(default=None, ge=0)
    recheck_metric: str | None = None
    recheck_source: str | None = Field(
        default=None,
        description="Connector or tool reference that performs the recheck (e.g. 'intune', 'crowdstrike')",
    )


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
    trigger_conditions: list[str] | None = None
    core_entities: list[str] | None = None
    observed_errors: list[str] | None = None
    root_causes: list[str] | None = None
    resolution_steps: list[str] | None = None
    evidence_summary: dict | None = None
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
    approval_policy_id: UUID | None
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
    verification_policy: dict | None = None
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
    approval_policy_id: UUID | None = None

    @field_validator("automation_mode")
    @classmethod
    def _check_automation_mode(cls, value: str) -> str:
        return _validate_automation_mode(value)


class PlaybookUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    risk_tier: str | None = None
    automation_mode: str | None = None
    approval_policy_id: UUID | None = None
    reviewer_user_id: UUID | None = None

    @field_validator("automation_mode")
    @classmethod
    def _check_automation_mode(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return _validate_automation_mode(value)


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
    steps: list[PlaybookStep] = Field(default_factory=list)
    rollback_notes: str | None = None
    evidence_refs: list | None = None
    playbook_confidence: float = 0.5
    execution_confidence_guidance: str | None = None
    verification_policy: VerificationPolicy | None = None


class RuntimeMatchRequest(BaseModel):
    symptoms: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)
    environment: dict = Field(default_factory=dict)
    context: str | None = None
    session_id: UUID | None = None
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
    retrieval_score: float | None = None
    playbook_confidence: float | None = None
    freshness_status: str
    evidence_count: int
    risk_tier: str
    automation_mode: str
    scoring_breakdown: dict | None = None


class RuntimeMatchResponse(BaseModel):
    match_id: str
    session_id: UUID | None = None
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


class PlaybookRollbackRequest(BaseModel):
    target_version_id: UUID


class PlaybookVersionDiffResponse(BaseModel):
    playbook_id: UUID
    base_version_id: UUID | None
    base_semantic_version: str | None
    target_version_id: UUID
    target_semantic_version: str
    changed_fields: list[str] = Field(default_factory=list)
    unified_diff: str
