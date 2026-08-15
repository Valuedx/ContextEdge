"""Criterion-level verification (F9).

The sweep answered one question — "did an incident or an alert reappear?" —
and turned it into one of three words on the run. That made two different
situations indistinguishable: a service that recovered and stayed quiet, and a
CI that stopped reporting. Both read as ``verified``, and both fed the cohort
counters and knowledge support as success.

An **observation** is one criterion evaluated over one window. An
**assessment** aggregates them. Splitting them is the point: "verified" with
no statement of what was checked is not a verifiable claim, and the learning
loop downstream deserves to know whether success meant *silence* or
*confirmation*.

**Criteria are not a table.** They are declared in
``PlaybookVersion.verification_policy`` (which already exists and is already
read) plus the defaults, and each observation records the criterion type and
parameters it evaluated. A ``verification_criteria`` table with no authoring
surface would be another set of columns nothing writes — the exact problem
this epic exists to close.

**Deviation from v6, recorded deliberately:** v6 lists ``ESCALATE_TO_HUMAN``
as an assessment result. Here escalation is a *flag* that can accompany any
result, because a verdict and a routing decision are different things and a
seventh result nothing emits would be vocabulary rather than behaviour.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
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
from sqlalchemy.orm import Mapped, mapped_column, relationship

from contextedge.models.base import Base

# What a criterion looks at. ``*_absence`` are negative signals — evidence of
# non-recurrence — and cannot on their own distinguish recovery from silence.
# ``user_confirmation`` is the first positive signal: somebody said it worked.
CRITERION_TYPES = (
    "incident_absence",
    "alert_absence",
    "user_confirmation",
)

# Positive signals: a pass means something was observed to be GOOD, not merely
# that nothing bad was observed. The distinction drives the SUCCESS vs
# INCONCLUSIVE split in the aggregation.
POSITIVE_CRITERION_TYPES = ("user_confirmation",)

OBSERVATION_STATUSES = ("pass", "fail", "inconclusive", "not_observable")

ASSESSMENT_RESULTS = (
    "success",
    "partial_success",
    "failed",
    "inconclusive",
    "monitor_required",
    "rollback_required",
)


class VerificationAssessment(Base):
    __tablename__ = "verification_assessments"
    __table_args__ = (
        CheckConstraint(
            "overall_result IN ('success', 'partial_success', 'failed', "
            "'inconclusive', 'monitor_required', 'rollback_required')",
            name="ck_verification_assessments_result",
        ),
        Index("ix_verification_assessments_tenant_run", "tenant_id", "execution_run_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    execution_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("execution_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    overall_result: Mapped[str] = mapped_column(String(30), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Routing, separate from the verdict. A failed verification with something
    # to undo is a different next step from one with nothing to undo.
    rollback_recommended: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    retry_recommended: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    escalation_required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    monitoring_window_sec: Mapped[int | None] = mapped_column(Integer, nullable=True)

    verified_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    verified_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    observations: Mapped[list[VerificationObservation]] = relationship(
        back_populates="assessment", cascade="all, delete-orphan"
    )


class VerificationObservation(Base):
    __tablename__ = "verification_observations"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pass', 'fail', 'inconclusive', 'not_observable')",
            name="ck_verification_observations_status",
        ),
        Index("ix_verification_observations_assessment", "assessment_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    assessment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("verification_assessments.id", ondelete="CASCADE"),
        nullable=False,
    )
    criterion_type: Mapped[str] = mapped_column(String(40), nullable=False)
    # The human-facing name of what was checked, e.g.
    # "no new incidents on vpn-gw-east-01".
    criterion_name: Mapped[str] = mapped_column(String(200), nullable=False)
    # The criterion's parameters as evaluated — the window, the CIs, the
    # thresholds. Without them a recorded status is unreproducible.
    criterion_params: Mapped[dict] = mapped_column(
        JSONB, server_default="{}", nullable=False
    )

    status: Mapped[str] = mapped_column(String(20), nullable=False)
    observed_value: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)

    window_start: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    window_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    assessment: Mapped[VerificationAssessment] = relationship(
        back_populates="observations"
    )
