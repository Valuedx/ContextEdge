"""Undoing, and handing over (F11).

Rollback was free text — ``PlaybookVersion.rollback_notes`` and a per-step
``rollback_hint`` — and ``reversible`` was a flag nothing consumed. Escalation
was an ``escalate_to_human`` decision type and an ``escalated`` case status, so
a human received a notification rather than the evidence.

**A rollback execution is an ``ExecutionRun``.** v6 models RollbackPlan /
RollbackAction / RollbackExecution as three new classes; here only the *plan*
is new. Running the undo needs steps, approvals, attempts, an artifact binding
and a verification — all of which F6–F9 just built for ``ExecutionRun``, and a
parallel execution hierarchy would duplicate every one of them and then drift.
``ExecutionRun.rolls_back_run_id`` is the whole difference, and it means a
rollback is verified like anything else rather than being trusted because it
was called a rollback.

**An escalation carries the bundle, not a message.** Refs, never copies: the
verification assessment, the decision trace, the rejected options, the evidence
ids. A copy would be a second version of the truth that ages, and the whole
point is that the human sees what the system saw.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from contextedge.models.base import Base, TenantScopedMixin

# proposed  — derived from a failed verification, nobody has looked yet.
# approved  — a human accepted it; it may be executed as its own run.
# executed  — an ExecutionRun exists with rolls_back_run_id set to the forward run.
# rejected  — a human decided not to undo.
# infeasible — nothing in the forward run is reversible; recorded rather than
#              hidden, because "we cannot undo this" is the most important
#              thing a responder can learn early.
ROLLBACK_PLAN_STATUSES = ("proposed", "approved", "executed", "rejected", "infeasible")

ESCALATION_STATUSES = ("open", "acknowledged", "resolved", "cancelled")
ESCALATION_PRIORITIES = ("low", "normal", "high", "critical")


class RollbackPlan(Base, TenantScopedMixin):
    """What undoing a run would involve, derived when verification fails."""

    __tablename__ = "rollback_plans"
    __table_args__ = (
        CheckConstraint(
            "status IN ('proposed', 'approved', 'executed', 'rejected', 'infeasible')",
            name="ck_rollback_plans_status",
        ),
        Index("ix_rollback_plans_tenant_status", "tenant_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    # The run this would undo.
    execution_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("execution_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    verification_assessment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("verification_assessments.id", ondelete="SET NULL"),
        nullable=True,
    )

    status: Mapped[str] = mapped_column(String(20), nullable=False, default="proposed")
    # One entry per forward step that can be undone, in REVERSE order, each
    # naming its rollback skill or its free-text hint and the step it reverses.
    # Ordered here rather than at read time because the order is the plan.
    actions: Mapped[list] = mapped_column(JSONB, server_default="[]", nullable=False)
    # Forward steps with no way back. Named explicitly: a plan that silently
    # omits them reads as complete when it is partial.
    irreversible_steps: Mapped[list] = mapped_column(
        JSONB, server_default="[]", nullable=False
    )
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    decided_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Escalation(Base, TenantScopedMixin):
    """A handover to a human, carrying what the system saw."""

    __tablename__ = "escalations"
    __table_args__ = (
        CheckConstraint(
            "status IN ('open', 'acknowledged', 'resolved', 'cancelled')",
            name="ck_escalations_status",
        ),
        CheckConstraint(
            "priority IN ('low', 'normal', 'high', 'critical')",
            name="ck_escalations_priority",
        ),
        Index("ix_escalations_tenant_status", "tenant_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    case_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("resolution_sessions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    execution_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("execution_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    decision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("decisions.id", ondelete="SET NULL"), nullable=True
    )

    reason: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[str] = mapped_column(String(20), nullable=False, default="normal")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open")

    escalated_by: Mapped[str] = mapped_column(String(120), nullable=False)
    # The role or queue, not a person: on-call rotates, and recording a name
    # makes the record wrong the moment the shift changes.
    escalated_to: Mapped[str | None] = mapped_column(String(120), nullable=True)

    # REFS, never copies — the verification assessment, the decision trace,
    # the options that were rejected and why, the evidence ids. A copy would
    # be a second version of the truth that ages away from the first.
    evidence_bundle: Mapped[dict] = mapped_column(
        JSONB, server_default="{}", nullable=False
    )
    # What the system would try next, so the human starts from a position
    # rather than from the beginning.
    recommended_next_actions: Mapped[list] = mapped_column(
        JSONB, server_default="[]", nullable=False
    )

    acknowledged_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    acknowledged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Minutes from raise to acknowledge, filled on acknowledgement. Stored
    # rather than computed so the number survives a later edit of either
    # timestamp, and so "how long do escalations sit?" is one query.
    acknowledgement_latency_min: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
