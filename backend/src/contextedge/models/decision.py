import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from contextedge.models.base import Base, TenantScopedMixin

DECISION_TYPES = (
    "classify_issue",
    "verify_dependency",
    "restart_workflow",
    "request_approval",
    "escalate_to_human",
    "create_ticket",
    "defer",
    "ask_clarifying_question",
    "execute_playbook",
    "select_playbook",
    "approve",
    "deny",
    "modify",
)

AGENT_STEPS = ("diagnostics", "remediation", "evaluation", "triage")

# The governance-oriented axis (0029). ``decision_type`` says what the system
# DID; ``decision_intent`` says what KIND of governed act it was, which is the
# axis a policy engine keys on. Derived deterministically from decision_type by
# ``INTENT_BY_DECISION_TYPE`` so the two can never drift, and overridable by
# callers that know better.
DECISION_INTENTS = (
    "diagnosis",
    "recommendation",
    "remediation",
    "escalation",
    "approval_request",
    "approval_decision",
    "clarification",
    "deferral",
)

INTENT_BY_DECISION_TYPE = {
    "classify_issue": "diagnosis",
    "verify_dependency": "diagnosis",
    "select_playbook": "recommendation",
    "restart_workflow": "remediation",
    "execute_playbook": "remediation",
    "create_ticket": "remediation",
    "request_approval": "approval_request",
    "approve": "approval_decision",
    "deny": "approval_decision",
    "modify": "approval_decision",
    "escalate_to_human": "escalation",
    "ask_clarifying_question": "clarification",
    "defer": "deferral",
}
ACTOR_TYPES = ("ai", "human", "hybrid")
DECISION_STATUSES = ("pending", "completed", "superseded", "reverted")
RISK_LEVELS = ("low", "medium", "high")
OUTCOME_RESULTS = ("success", "failure", "partial", "timeout", "rejected")

REJECTION_REASON_CODES = (
    "wrong_diagnosis",
    "plan_incomplete",
    "needs_human_judgment",
    "user_context_missing",
    "policy_violation",
    "other",
)


class Decision(Base, TenantScopedMixin):
    __tablename__ = "decisions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True,
    )
    domain_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("domains.id"), nullable=True, index=True,
    )
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("resolution_sessions.id"),
        nullable=True,
        index=True,
    )
    parent_decision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("decisions.id"),
        nullable=True,
        index=True,
    )

    decision_type: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    agent_step: Mapped[str] = mapped_column(String(30), nullable=False)
    actor_type: Mapped[str] = mapped_column(String(20), nullable=False, default="ai")
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True,
    )

    context_snapshot: Mapped[dict] = mapped_column(
        JSONB, server_default="{}", nullable=False,
    )
    evidence_summary: Mapped[list] = mapped_column(
        JSONB, server_default="[]", nullable=False,
    )
    rationale_summary: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    uncertainty_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    compact_trace: Mapped[str | None] = mapped_column(Text, nullable=True)
    explanation: Mapped[str | None] = mapped_column(Text, nullable=True)

    approval_required: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False,
    )
    policy_refs: Mapped[list] = mapped_column(
        JSONB, server_default="[]", nullable=False,
    )
    human_override: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(30), default="pending", nullable=False, index=True,
    )

    embedding = mapped_column(Vector(3072), nullable=True)

    # AE Ops Context Graph alignment. The existing fields encode the
    # *action-oriented* decision (classify_issue, restart_workflow, …);
    # ``decision_intent`` encodes the *governance-oriented* axis the
    # design's policy engine queries against (diagnosis, recommendation,
    # remediation, …). Both stay populated to avoid widening the existing
    # ``decision_type`` enum.
    decision_intent: Mapped[str | None] = mapped_column(
        String(40), nullable=True, index=True
    )
    decision_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Trace-level risk distinct from per-option risk on DecisionOption.
    risk_level: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # Verdict, not pointer. The ``policy_refs JSONB[]`` above keeps the
    # pointers; this column is the value the executor checks.
    policy_result: Mapped[str | None] = mapped_column(
        String(40), nullable=True, index=True
    )

    options: Mapped[list["DecisionOption"]] = relationship(
        back_populates="decision",
        order_by="DecisionOption.created_at",
        cascade="all, delete-orphan",
    )
    outcomes: Mapped[list["DecisionOutcome"]] = relationship(
        back_populates="decision",
        foreign_keys="DecisionOutcome.decision_id",
        order_by="DecisionOutcome.created_at",
        cascade="all, delete-orphan",
    )
    children: Mapped[list["Decision"]] = relationship(
        back_populates="parent",
        foreign_keys="Decision.parent_decision_id",
    )
    parent: Mapped["Decision | None"] = relationship(
        back_populates="children",
        remote_side="Decision.id",
        foreign_keys="Decision.parent_decision_id",
    )


class DecisionOption(Base):
    __tablename__ = "decision_options"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    decision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("decisions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True,
    )

    action: Mapped[str] = mapped_column(String(200), nullable=False)
    suitability: Mapped[float | None] = mapped_column(Float, nullable=True)
    risk_level: Mapped[str | None] = mapped_column(String(20), nullable=True)
    preconditions: Mapped[list] = mapped_column(
        JSONB, server_default="[]", nullable=False,
    )
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    rejection_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    selected: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )

    decision: Mapped["Decision"] = relationship(back_populates="options")


class DecisionOutcome(Base):
    __tablename__ = "decision_outcomes"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    decision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("decisions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True,
    )

    action_executed: Mapped[str] = mapped_column(String(200), nullable=False)
    execution_result: Mapped[str] = mapped_column(String(30), nullable=False)
    result_details: Mapped[dict] = mapped_column(
        JSONB, server_default="{}", nullable=False,
    )
    follow_up_needed: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False,
    )
    follow_up_decision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("decisions.id"),
        nullable=True,
    )
    feedback_received: Mapped[str | None] = mapped_column(Text, nullable=True)
    feedback_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    feedback_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )

    decision: Mapped["Decision"] = relationship(
        back_populates="outcomes",
        foreign_keys=[decision_id],
    )
