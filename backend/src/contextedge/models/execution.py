import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from contextedge.models.base import Base, TenantScopedMixin

SAFETY_CLASSES = ("read_only", "low_side_effect", "high_side_effect", "destructive")
EXECUTION_STATUSES = ("pending", "running", "awaiting_approval", "completed", "failed", "aborted")
STEP_STATUSES = ("pending", "running", "awaiting_approval", "completed", "skipped", "failed")
APPROVAL_STATUSES = ("pending", "approved", "denied")
OUTCOMES = ("success", "partial", "failure", "aborted")


class ExecutionRun(Base, TenantScopedMixin):
    __tablename__ = "execution_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True,
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
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True,
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
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True,
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
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True,
    )
    requested_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    requested_action: Mapped[str] = mapped_column(String(120), nullable=False)
    safety_class: Mapped[str] = mapped_column(String(30), nullable=False)
    context: Mapped[dict] = mapped_column(JSONB, server_default="{}", nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="pending", nullable=False, index=True)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )

    execution_run: Mapped["ExecutionRun"] = relationship(back_populates="approval_requests")
    step_run: Mapped["ExecutionStepRun | None"] = relationship(back_populates="approval_requests")
