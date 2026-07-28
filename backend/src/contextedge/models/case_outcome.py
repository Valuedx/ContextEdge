"""Case-level outcome and case state-transition history.

Distinct from ``DecisionOutcome``: that records "did this specific
decision execute OK?". ``CaseOutcome`` records "is the case actually
resolved, and what should we learn?". Both coexist — they answer
different questions in the audit trail.

``CaseStateTransition`` is the optional history complement to
``resolution_sessions.status`` — without it the column is current-state
only and the lifecycle (Section 43.21) is unobservable.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from contextedge.models.base import Base


OUTCOME_STATUSES = (
    "resolved",
    "unresolved",
    "workaround_applied",
    "escalated",
    "duplicate",
    "false_alarm",
)

CASE_STATUSES = (
    "new",
    "triaging",
    "diagnosing",
    "awaiting_user_clarification",
    "awaiting_approval",
    "approved",
    "executing",
    "monitoring",
    "resolved",
    "closed",
    "escalated",
    "cancelled",
    "reopened",
)


class CaseOutcome(Base):
    """Case-level outcome row. One per case at close time; reopen creates a new one."""

    __tablename__ = "case_outcomes"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("resolution_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    outcome_status: Mapped[str] = mapped_column(
        String(40), nullable=False, index=True
    )
    resolution_summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    confirmed_root_cause: Mapped[str | None] = mapped_column(Text, nullable=True)
    successful_action: Mapped[str | None] = mapped_column(String(120), nullable=True)
    failed_actions: Mapped[list] = mapped_column(
        JSONB, server_default="[]", nullable=False
    )

    user_confirmed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    mttr_minutes: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)

    closed_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    closed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    should_create_or_update_pattern: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )

    attributes: Mapped[dict] = mapped_column(
        JSONB, server_default="{}", nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class CaseStateTransition(Base):
    """Append-only history of ``resolution_sessions.status`` transitions."""

    __tablename__ = "case_state_transitions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("resolution_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    from_status: Mapped[str | None] = mapped_column(String(40), nullable=True)
    to_status: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    transition_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    transitioned_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class CaseOutcomeFixPattern(Base):
    __tablename__ = "case_outcome_fix_patterns"
    __table_args__ = (
        UniqueConstraint(
            "case_outcome_id",
            "fix_pattern_id",
            "result",
            name="uq_case_outcome_fix_patterns_outcome_fix_result",
        ),
        CheckConstraint(
            "result IN ('successful', 'failed', 'partial')",
            name="ck_case_outcome_fix_patterns_result",
        ),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_case_outcome_fix_patterns_confidence",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )
    case_outcome_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("case_outcomes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    fix_pattern_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("fix_patterns.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    result: Mapped[str] = mapped_column(String(20), nullable=False)
    confidence: Mapped[float | None] = mapped_column(Numeric(5, 4), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
