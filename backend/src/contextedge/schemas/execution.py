from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from contextedge.models.decision import REJECTION_REASON_CODES


class StartExecutionRequest(BaseModel):
    playbook_id: UUID
    playbook_version_id: UUID | None = None
    session_id: UUID | None = None
    max_safety_class: str = Field(
        "read_only",
        description="Maximum safety class the caller authorises: read_only, low_side_effect, high_side_effect, destructive",
    )


class ApprovalDecision(BaseModel):
    decision: str = Field(..., description="approved or denied")
    comment: str | None = None


class ApprovalModificationRequest(BaseModel):
    """Approve-with-changes on a pending approval request.

    `modification_diff` describes what's changing (e.g. `{"inputs": {...}, "summary": "..."}`).
    Only the `inputs` key is applied automatically to the step run; other keys
    are persisted verbatim for audit. `modification_reason_code` uses the same
    enum as reject so analytics can compare across verbs.
    """

    modification_diff: dict
    modification_reason_code: str = Field(
        ...,
        description=f"One of {REJECTION_REASON_CODES}",
    )
    comment: str | None = None

    @field_validator("modification_diff")
    @classmethod
    def _validate_diff(cls, v: dict) -> dict:
        if not v:
            raise ValueError("modification_diff must be a non-empty object")
        return v

    @field_validator("modification_reason_code")
    @classmethod
    def _validate_code(cls, v: str) -> str:
        if v not in REJECTION_REASON_CODES:
            raise ValueError(
                f"modification_reason_code must be one of {REJECTION_REASON_CODES}",
            )
        return v


class ToolInvocationResponse(BaseModel):
    id: UUID
    step_run_id: UUID
    tool_name: str
    tool_version: str | None
    safety_class: str
    inputs: dict
    outputs: dict
    status: str
    error_message: str | None
    duration_ms: int | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ExecutionStepRunResponse(BaseModel):
    id: UUID
    execution_run_id: UUID
    step_index: int
    step_title: str | None
    safety_class: str
    requires_approval: bool
    status: str
    inputs: dict
    outputs: dict
    error_message: str | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    tool_invocations: list[ToolInvocationResponse] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class ApprovalRequestResponse(BaseModel):
    id: UUID
    execution_run_id: UUID
    step_run_id: UUID | None
    requested_by: UUID
    requested_action: str
    safety_class: str
    context: dict
    status: str
    decided_by: UUID | None
    decided_at: datetime | None
    decision_comment: str | None
    modification_diff: dict | None = None
    modification_reason_code: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class ExecutionRunResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    session_id: UUID | None
    playbook_id: UUID
    playbook_version_id: UUID
    initiated_by: UUID
    status: str
    automation_mode: str
    max_safety_class: str
    started_at: datetime | None
    completed_at: datetime | None
    outcome: str | None
    outcome_summary: str | None
    # Post-action verification (0036): did the fix hold? NULL until the
    # sweep has re-checked operational reality on the session's CIs.
    verification_status: str | None = None
    verified_at: datetime | None = None
    verification_details: dict | None = None
    created_at: datetime
    step_runs: list[ExecutionStepRunResponse] = Field(default_factory=list)
    approval_requests: list[ApprovalRequestResponse] = Field(default_factory=list)

    model_config = {"from_attributes": True}
