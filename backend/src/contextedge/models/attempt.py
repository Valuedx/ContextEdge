"""Retries as a first-class thing (F8).

``ExecutionStepRun`` carries one status. A step that timed out and was retried
had nowhere to say so: the second try overwrote the first, and "did this run
twice?" was unanswerable from the ledger. v6 §26.2 makes attempts first-class
for exactly this reason, and F8's duplicate suppression needs somewhere to
record that a replay was *recognised and refused* rather than silently dropped.

An attempt is the unit that either happened or did not. The step-run above it
is the intent; the tool invocation below it is the call. A ``DEDUPLICATED``
attempt is the important one: it is the durable evidence that a replay arrived
and was suppressed, which is the difference between an idempotency control that
works and one nobody can prove worked.
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
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from contextedge.models.base import Base

# The technical result of one attempt — never the business outcome. A
# SUCCEEDED attempt can still be a failed remediation, which is what the
# verification plane is for.
ATTEMPT_STATUSES = (
    "running",
    "succeeded",
    "failed",
    "timeout",
    "cancelled",
    "deduplicated",
)

# Statuses that mean the attempt is over.
TERMINAL_ATTEMPT_STATUSES = ("succeeded", "failed", "timeout", "cancelled", "deduplicated")


class ExecutionAttempt(Base):
    __tablename__ = "execution_attempts"
    __table_args__ = (
        UniqueConstraint(
            "step_run_id", "attempt_number", name="uq_execution_attempts_step_number"
        ),
        CheckConstraint("attempt_number >= 1", name="ck_execution_attempts_number"),
        CheckConstraint(
            "status IN ('running', 'succeeded', 'failed', 'timeout', 'cancelled', "
            "'deduplicated')",
            name="ck_execution_attempts_status",
        ),
        Index("ix_execution_attempts_tenant_started", "tenant_id", "started_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    step_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("execution_step_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)

    # Which skill, at which version, this attempt invoked. Recorded on the
    # attempt rather than read from the step at audit time because the skill
    # can be superseded between the attempt and the question.
    skill_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("skills.id", ondelete="SET NULL"), nullable=True
    )
    skill_version: Mapped[str | None] = mapped_column(String(20), nullable=True)

    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    # The artifact hash the attempt ran against (F7). Two attempts of the same
    # step with different input hashes means the payload changed mid-flight.
    input_hash: Mapped[str | None] = mapped_column(String(71), nullable=True)
    # Whoever ran it — a worker id, a service name, a human. Free text because
    # the executor does not exist yet and inventing its identity scheme here
    # would be inventing the wrong one.
    worker_ref: Mapped[str | None] = mapped_column(String(200), nullable=True)

    status: Mapped[str] = mapped_column(String(20), nullable=False, default="running")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # For a deduplicated attempt: the step-run this replay collided with.
    duplicate_of_step_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
