import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from contextedge.models.base import Base, TenantScopedMixin

SAFETY_CLASSES = ("read_only", "low_side_effect", "high_side_effect", "destructive")
EXECUTION_STATUSES = ("pending", "running", "awaiting_approval", "completed", "failed", "aborted")
STEP_STATUSES = ("pending", "running", "awaiting_approval", "completed", "skipped", "failed")
# ``expired`` is written by ``approval_expiry_service`` when a pending request
# ages out (72h). It was absent from this tuple until the F1 writer audit —
# the vocabulary has to describe what the code actually writes.
APPROVAL_STATUSES = ("pending", "approved", "denied", "modified", "expired")
OUTCOMES = ("success", "partial", "failure", "aborted")

# What a step *is*, as opposed to what it is called (``action_name``) or how
# dangerous it is (``safety_class``). Declared by the playbook author on the
# step; never inferred — a step that does not declare one stores NULL rather
# than a guess, because the policy engine (F3) will key on it.
ACTION_TYPES = (
    "diagnostic",
    "remediation",
    "notification",
    "escalation",
    "approval",
    "manual",
)


class ExecutionRun(Base, TenantScopedMixin):
    __tablename__ = "execution_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("resolution_sessions.id"), nullable=True, index=True,
    )
    playbook_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("playbooks.id"), nullable=False, index=True,
    )
    playbook_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("playbook_versions.id"), nullable=False,
    )
    initiated_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="pending", nullable=False, index=True)
    automation_mode: Mapped[str] = mapped_column(String(30), default="suggest_only", nullable=False)
    max_safety_class: Mapped[str] = mapped_column(String(30), default="read_only", nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    outcome: Mapped[str | None] = mapped_column(String(30), nullable=True)
    outcome_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Post-action verification (migration 0036): did the fix HOLD?
    # NULL = not yet checked (the sweep's queue); then verified | failed |
    # unverifiable. Written by execution_verification_service.
    verification_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verification_details: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # F11: a rollback IS an execution — it needs steps, approvals, attempts,
    # an artifact binding and a verification, all of which this table already
    # has. This column is the whole difference between a run and the run that
    # undoes it, and it means a rollback is verified like anything else rather
    # than trusted because it was called a rollback.
    rolls_back_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("execution_runs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )

    step_runs: Mapped[list["ExecutionStepRun"]] = relationship(
        back_populates="execution_run", order_by="ExecutionStepRun.step_index",
    )
    approval_requests: Mapped[list["ApprovalRequest"]] = relationship(
        back_populates="execution_run",
    )


class ExecutionStepRun(Base):
    __tablename__ = "execution_step_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    execution_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("execution_runs.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    step_index: Mapped[int] = mapped_column(Integer, nullable=False)
    step_title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    safety_class: Mapped[str] = mapped_column(String(30), default="read_only", nullable=False)
    requires_approval: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="pending", nullable=False)
    inputs: Mapped[dict] = mapped_column(JSONB, server_default="{}", nullable=False)
    outputs: Mapped[dict] = mapped_column(JSONB, server_default="{}", nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )

    # AE Ops Context Graph alignment.
    # ``action_name`` is the controlled identifier the policy engine
    # matches against (e.g. ``rerun_workflow``, ``resend_existing_output``);
    # ``step_title`` stays as the human-readable label.
    action_name: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    action_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # Denormalised from ExecutionRun.automation_mode so each step row is
    # self-describing without a join.
    execution_mode: Mapped[str | None] = mapped_column(String(40), nullable=True)
    executed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    # Banking-grade duplicate-prevention. Partial unique index ensures
    # only NOT NULL keys are constrained (added in migration).
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    duplicate_check_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    # Anchor to case + decision so any action row is queryable from
    # either side of the chain.
    case_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("resolution_sessions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    decision_trace_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("decisions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    execution_run: Mapped["ExecutionRun"] = relationship(back_populates="step_runs")
    tool_invocations: Mapped[list["ToolInvocation"]] = relationship(back_populates="step_run")
    approval_requests: Mapped[list["ApprovalRequest"]] = relationship(back_populates="step_run")


class ToolInvocation(Base):
    __tablename__ = "tool_invocations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    step_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("execution_step_runs.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    tool_name: Mapped[str] = mapped_column(String(200), nullable=False)
    tool_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    safety_class: Mapped[str] = mapped_column(String(30), default="read_only", nullable=False)
    inputs: Mapped[dict] = mapped_column(JSONB, server_default="{}", nullable=False)
    outputs: Mapped[dict] = mapped_column(JSONB, server_default="{}", nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="pending", nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )

    step_run: Mapped["ExecutionStepRun"] = relationship(back_populates="tool_invocations")


class ApprovalRequest(Base):
    __tablename__ = "approval_requests"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    execution_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("execution_runs.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    step_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("execution_step_runs.id", ondelete="CASCADE"),
        nullable=True,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    requested_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    requested_action: Mapped[str] = mapped_column(String(120), nullable=False)
    safety_class: Mapped[str] = mapped_column(String(30), nullable=False)
    context: Mapped[dict] = mapped_column(JSONB, server_default="{}", nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="pending", nullable=False, index=True)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    modification_diff: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    modification_reason_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )

    # AE Ops Context Graph alignment.
    # ``action_name`` is the controlled identifier (vs free-text
    # ``requested_action``); ``approver_role`` is the *role consulted*
    # not the user; ``approval_channel`` is the surface where the
    # approval flowed (teams/email/servicenow/portal/manual).
    action_name: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    approver_role: Mapped[str | None] = mapped_column(String(120), nullable=True)
    approval_channel: Mapped[str | None] = mapped_column(String(40), nullable=True)
    approval_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Segregation of duties (Section 43.13). The agent/human that
    # recommended is recorded separately from approval/execution so the
    # SoD check can detect violations.
    recommended_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    executed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    sod_check_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    sod_violation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # F7 — what was approved, exactly. ``artifact_hash`` is an RFC 8785
    # canonicalization of the step in its version, re-checked immediately
    # before the tool runs; ``policy_snapshot`` is the governance state the
    # approver decided under; ``expires_at`` is when the ANSWER goes stale
    # (distinct from the 72h that expires an unanswered request).
    artifact_version: Mapped[str | None] = mapped_column(String(20), nullable=True)
    artifact_hash: Mapped[str | None] = mapped_column(String(71), nullable=True)
    policy_snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Anchor approval to case + decision (currently only execution_run).
    case_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("resolution_sessions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    decision_trace_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("decisions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    execution_run: Mapped["ExecutionRun"] = relationship(back_populates="approval_requests")
    step_run: Mapped["ExecutionStepRun | None"] = relationship(back_populates="approval_requests")
