"""Operational situations: what is happening NOW.

An `Episode` is an empirical reconstruction of a resolution experience —
what happened, what was done, whether it worked. A `KnowledgeCase` is what
a document claims. An `OperationalSituation` is neither: it is a bounded
real-world occurrence, still unfolding, assembled from the signals that
appear to describe it.

    Situation   what is happening
    Episode     what happened, and what worked
    KnowledgeCase  what a source says works

The distinction that matters most in code: a situation may exist while
nothing is resolved, and it must NOT become an episode merely by existing.
An episode needs a resolution to reconstruct; a situation that never
resolves has nothing empirical to say.

Separate from CorrelationEdge on purpose. A correlation edge means "these
two pieces of evidence appear related". A situation means "these many
signals collectively describe one occurrence". Renaming the former into
the latter would assert something the evidence does not support.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from contextedge.models.base import Base, TenantScopedMixin

# --- vocabularies ---------------------------------------------------------
# Free-text columns with the accepted values named here rather than as
# database enums: the repository's other lifecycle columns work the same
# way, and an enum migration for a vocabulary still being learned costs
# more than it protects.

SITUATION_TYPES = (
    "outage",
    "degradation",
    "incident_storm",
    "change_failure",
    "infrastructure_failure",
    "security_event",
    "recurring_issue",
    "unknown",
)

SITUATION_STATES = (
    "emerging",      # signals suggest a shared occurrence; evidence thin
    "active",        # authoritative linkage or strong multi-signal evidence
    "stabilizing",   # recovery evidence, NOT merely fewer signals arriving
    "resolved",      # verified, NOT merely quiet
    "reopened",
    "merged",
    "invalidated",
)

# What a piece of evidence is doing in this situation.
EVIDENCE_ROLES = (
    "primary_incident",
    "related_incident",
    "monitoring_alert",
    "monitoring_event",
    "problem_record",
    "communication",
    "supporting_event",
    "change_candidate",
    "remediation_change",
    "recovery_signal",
)

# Membership is not binary. Evidence can sit provisionally in more than one
# candidate situation until something decides; forcing an early choice is
# how a false merge becomes permanent.
MEMBERSHIP_STATUSES = ("confirmed", "inferred", "provisional", "rejected", "retired")

IMPACT_ROLES = (
    "primary_affected",
    "affected",
    "downstream_affected",
    "shared_dependency",
    "suspected_root_component",
    "healthy_control",  # what appears FINE — narrows RCA as much as what is broken
    "business_service",
    "infrastructure_dependency",
)

# A change is a candidate until something authoritative says otherwise.
# `confirmed` is reachable only from governed evidence — an ITSM caused-by
# relationship, an approved RCA, a human decision — never from a score and
# never from an agent's opinion.
CHANGE_CANDIDATE_STATUSES = (
    "weak_candidate",
    "candidate",
    "suspected",
    "corroborated",
    "confirmed",
    "rejected",
    "remediation",  # acted on the situation rather than caused it
    "rollback",
)

TEMPORAL_RELATIONS = ("before_onset", "overlaps_onset", "after_onset", "unknown")


class OperationalSituation(Base, TenantScopedMixin):
    __tablename__ = "operational_situations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    domain_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("domains.id"), nullable=True
    )

    situation_type: Mapped[str] = mapped_column(
        String(40), default="unknown", nullable=False
    )
    state: Mapped[str] = mapped_column(String(20), default="emerging", nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    severity: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # How strongly the signals support this being ONE occurrence. Distinct
    # from a membership's own confidence and from a change candidate's
    # score — collapsing them into one number would make every downstream
    # reader guess which question it answers.
    situation_confidence: Mapped[float] = mapped_column(
        Float, default=0.0, nullable=False
    )

    # onset_at is when the occurrence began in the WORLD; detected_at is
    # when we first saw it. Late-arriving evidence can move onset backwards
    # and must be able to, or causality classification is computed against
    # the wrong instant.
    onset_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    detected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_signal_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    stabilizing_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    primary_entity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("entities.id"), nullable=True
    )
    primary_service_entity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("entities.id"), nullable=True
    )

    incident_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    alert_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    event_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    change_candidate_count: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    affected_entity_count: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )

    # A lookup and duplicate-suppression key, NOT identity. Two situations
    # can share a fingerprint and still be different occurrences — the same
    # service can fail twice in one window for unrelated reasons — so this
    # is deliberately not unique.
    fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Which scoring model produced this. Persisted so a situation scored
    # last month stays explainable after the weights change.
    correlation_version: Mapped[str | None] = mapped_column(String(40), nullable=True)

    merged_into_situation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("operational_situations.id"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    memberships: Mapped[list["SituationEvidenceMembership"]] = relationship(
        back_populates="situation"
    )

    __table_args__ = (
        Index("ix_situations_tenant_state_signal", "tenant_id", "state", "last_signal_at"),
        Index("ix_situations_tenant_fingerprint", "tenant_id", "fingerprint"),
        # A merged situation must say what it merged into, and one that has
        # not merged must not pretend to. Merged rows are never deleted:
        # they are how "why did these two become one" stays answerable.
        CheckConstraint(
            "(state = 'merged' AND merged_into_situation_id IS NOT NULL)"
            " OR (state <> 'merged' AND merged_into_situation_id IS NULL)",
            name="ck_situation_merged_has_target",
        ),
    )


class SituationEvidenceMembership(Base, TenantScopedMixin):
    """Why one piece of evidence is considered part of one situation."""

    __tablename__ = "situation_evidence_memberships"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    situation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("operational_situations.id"),
        nullable=False,
        index=True,
    )
    evidence_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("evidence_items.id"), nullable=False, index=True
    )

    evidence_role: Mapped[str] = mapped_column(String(40), nullable=False)
    membership_status: Mapped[str] = mapped_column(
        String(20), default="provisional", nullable=False
    )
    membership_confidence: Mapped[float] = mapped_column(
        Float, default=0.0, nullable=False
    )
    correlation_method: Mapped[str | None] = mapped_column(String(60), nullable=True)

    # The decomposed score, not just the total. "Why was INC1002 associated
    # with SIT44" has to be answerable, and an opaque 0.87 does not answer
    # it. Also the calibration dataset: machine features beside the human
    # disposition is what a future weighting can be learned from.
    score_breakdown: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Records derived from ONE source event — a monitoring alert, the
    # ticket it opened, the mail it sent — share a lineage group so
    # confidence does not count one observation three times. The opposite
    # case, three independent monitoring systems agreeing, has three
    # different groups and should count for more.
    source_lineage_group: Mapped[str | None] = mapped_column(String(64), nullable=True)

    first_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    machine_decision_version: Mapped[str | None] = mapped_column(
        String(40), nullable=True
    )

    # A rejected membership is kept, never deleted: the machine score
    # beside the human verdict is the only record of what the model got
    # wrong, and that is the data any future calibration needs.
    review_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    review_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    situation: Mapped["OperationalSituation"] = relationship(
        back_populates="memberships"
    )

    __table_args__ = (
        # Idempotency: re-running the evaluator for the same evidence must
        # not add a second membership. One row per (situation, evidence),
        # updated in place.
        Index(
            "uq_situation_membership",
            "situation_id",
            "evidence_id",
            unique=True,
        ),
        Index("ix_situation_membership_evidence", "tenant_id", "evidence_id"),
        Index("ix_situation_membership_lineage", "situation_id", "source_lineage_group"),
    )


class SituationEntityImpact(Base, TenantScopedMixin):
    """What a situation appears to affect — and what appears fine."""

    __tablename__ = "situation_entity_impacts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    situation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("operational_situations.id"),
        nullable=False,
        index=True,
    )
    entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("entities.id"), nullable=False, index=True
    )

    impact_role: Mapped[str] = mapped_column(String(40), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    topology_distance: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # What this claim rests on, so "why do you think the database is fine"
    # has an answer.
    basis: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Freshness is load-bearing for healthy_control specifically. "Database
    # healthy" is a useful fact when the last signal is two minutes old and
    # a dangerous one when it is eight hours old — the claim has to carry
    # its own age or a reader cannot tell those apart.
    signal_observed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    first_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index(
            "uq_situation_entity_impact",
            "situation_id",
            "entity_id",
            "impact_role",
            unique=True,
        ),
        Index("ix_situation_impact_entity", "tenant_id", "entity_id", "situation_id"),
    )


class SituationChangeCandidate(Base, TenantScopedMixin):
    """A change that might explain a situation — until something says so."""

    __tablename__ = "situation_change_candidates"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    situation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("operational_situations.id"),
        nullable=False,
        index=True,
    )
    change_evidence_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("evidence_items.id"), nullable=False, index=True
    )

    status: Mapped[str] = mapped_column(
        String(20), default="candidate", nullable=False
    )

    # A RANKING, not a probability. 0.86 means "strong under the current
    # explainable model", never "86% likely to be the cause". Anything
    # rendering this to a human or an agent must use candidate language;
    # the day a calibrated probabilistic model exists it gets its own
    # column rather than quietly redefining this one.
    correlation_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    temporal_relation: Mapped[str] = mapped_column(
        String(20), default="unknown", nullable=False
    )
    minutes_from_onset: Mapped[int | None] = mapped_column(Integer, nullable=True)
    topology_distance: Mapped[int | None] = mapped_column(Integer, nullable=True)

    score_breakdown: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    reason_summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    # What makes this CONFIRMED, when it is: an ITSM caused-by relation, an
    # approved RCA, a governed human decision. Never a score, and never an
    # agent's opinion — that would let agent output launder itself into
    # agent input, which the decision projection already refuses to allow.
    confirmation_basis: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index(
            "uq_situation_change_candidate",
            "situation_id",
            "change_evidence_id",
            unique=True,
        ),
        Index(
            "ix_situation_change_rank", "tenant_id", "situation_id", "correlation_score"
        ),
        Index("ix_situation_change_evidence", "tenant_id", "change_evidence_id"),
        # A change that happened AFTER onset cannot have caused it. It can
        # be remediation, a rollback, or a diagnostic action — but not the
        # original cause, and the database refuses to record otherwise.
        CheckConstraint(
            "NOT (temporal_relation = 'after_onset'"
            " AND status IN ('suspected', 'corroborated', 'confirmed'))",
            name="ck_change_after_onset_not_causal",
        ),
    )
