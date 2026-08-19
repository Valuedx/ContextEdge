import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from contextedge.models.base import Base, TenantScopedMixin


class Pattern(Base, TenantScopedMixin):
    __tablename__ = "patterns"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id"),
        nullable=False,
        index=True,
    )
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    domain_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("domains.id"),
        nullable=True,
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    pattern_type: Mapped[str] = mapped_column(String(50), default="recurring_issue", nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    episode_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    active_flag: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    contradiction_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    freshness_score: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)

    trigger_conditions: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    core_entities: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    observed_errors: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    root_causes: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    resolution_steps: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    evidence_summary: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # What generated this pattern (0055) — see Episode.generation_provenance.
    generation_provenance: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    evidence_links: Mapped[list["PatternEvidenceLink"]] = relationship(back_populates="pattern")


class PatternEvidenceLink(Base):
    __tablename__ = "pattern_evidence_links"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    pattern_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("patterns.id"),
        nullable=False,
        index=True,
    )
    episode_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("episodes.id"),
        nullable=True,
    )
    evidence_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    link_type: Mapped[str] = mapped_column(String(50), nullable=False)
    weight: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    pattern: Mapped["Pattern"] = relationship(back_populates="evidence_links")


class PatternEvidence(Base, TenantScopedMixin):
    """Why a pattern believes what it believes.

    ``PatternEvidenceLink`` records THAT an episode belongs to a pattern.
    This records what a piece of evidence contributes and how much that
    contribution is worth — which of the pattern's claims it supports, and
    on what epistemic footing.

    The distinction that motivates the table: a pattern supported by three
    KB articles and a pattern supported by nineteen resolved incidents are
    not the same pattern, and a single ``episode_count`` cannot tell them
    apart. With this ledger a pattern can say "2 sources document this, 1
    SOP prescribes it, 19 episodes observed it, 14 of those succeeded and 3
    contradicted it" — which is what makes two things possible that a bare
    count cannot support:

    - **Cold start.** A pattern can exist on documented support alone
      (``evidence_class='documented'``, no empirical rows) and *graduate*
      as incidents arrive. The pattern graduates; the knowledge case does
      not. KC-17 stays permanently "documentation said this".
    - **Knowledge drift.** When a documented resolution accumulates
      ``contradicts_resolution`` rows from recent episodes while the
      article remains approved upstream, that gap is detectable — the KB
      is stale, and nothing else in the system would have noticed.

    Polymorphic by ``(evidence_object_type, evidence_object_id)`` rather
    than one nullable FK per type, because the set of contributors is
    expected to grow (procedures, vendor advisories, postmortem findings,
    automation executions) and a column per kind does not survive that.
    """

    __tablename__ = "pattern_evidence"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )
    pattern_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("patterns.id"), nullable=False, index=True
    )

    # episode | knowledge_case | procedure | ...
    evidence_object_type: Mapped[str] = mapped_column(String(40), nullable=False)
    evidence_object_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )

    # What this evidence is being offered FOR. A row that contradicts a
    # resolution is evidence too, and losing it is how a pattern keeps
    # recommending something that stopped working.
    support_role: Mapped[str] = mapped_column(
        String(40), default="supports_resolution", nullable=False
    )
    # empirical | documented | prescriptive | conversational | inferred.
    # Derived from the object type, never from the model's opinion: only an
    # episode may be empirical.
    evidence_class: Mapped[str] = mapped_column(String(20), nullable=False)

    strength: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)
    # For an episode: when the thing actually happened. NULL for documented
    # and prescriptive evidence, which has no occurrence — recency of a
    # document is `source_updated_at` on the case, a different question.
    observed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # For empirical rows: success | partial | failure | unknown. Always
    # NULL for non-empirical classes, enforced below.
    outcome: Mapped[str | None] = mapped_column(String(20), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index(
            "uq_pattern_evidence_object",
            "pattern_id",
            "evidence_object_type",
            "evidence_object_id",
            "support_role",
            unique=True,
        ),
        Index("ix_pattern_evidence_class", "pattern_id", "evidence_class"),
        # The invariant the whole split exists to protect, placed where it
        # cannot be forgotten: only an episode carries an outcome, and only
        # an episode may be empirical. A documented claim can never become
        # an observed success by a later code path setting a field.
        CheckConstraint(
            "(evidence_class = 'empirical' AND evidence_object_type = 'episode')"
            " OR (evidence_class <> 'empirical' AND outcome IS NULL)",
            name="ck_pattern_evidence_empirical_is_episode",
        ),
    )


class NegativeKnowledgeItem(Base, TenantScopedMixin):
    __tablename__ = "negative_knowledge_items"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id"),
        nullable=False,
        index=True,
    )
    domain_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    step_text: Mapped[str] = mapped_column(Text, nullable=False)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="ineffective", nullable=False)
    evidence_refs: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(50), nullable=True)


class Contradiction(Base, TenantScopedMixin):
    __tablename__ = "contradictions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id"),
        nullable=False,
        index=True,
    )
    source_a_ref: Mapped[str] = mapped_column(String(500), nullable=False)
    source_b_ref: Mapped[str] = mapped_column(String(500), nullable=False)
    contradiction_type: Mapped[str] = mapped_column(String(50), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolution_status: Mapped[str] = mapped_column(String(30), default="unresolved", nullable=False)
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)


class ContradictionScanState(Base, TenantScopedMixin):
    """Tracks which (playbook_version, evidence) pairs have been scanned for
    contradictions and what the outcome was.

    Used by ``contradiction_service.scan_contradictions`` to:

    - Skip pairs already scanned since the evidence's last update (incremental).
    - Record "skipped" outcomes (token-overlap gate, budget cap) so operators
      can see queue depth without re-scanning.
    - Age out stale results when evidence is re-ingested — callers check
      ``last_scanned_at >= evidence.updated_at`` and re-scan when false.

    A new ``playbook_version_id`` (i.e. a published new version of the
    playbook) implicitly invalidates all prior rows for the old version
    because rows are keyed on the version id, not the playbook id. The
    scan then starts fresh for the new version.
    """

    __tablename__ = "contradiction_scan_state"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id"),
        nullable=False,
        index=True,
    )
    playbook_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("playbook_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    evidence_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("evidence_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    last_scanned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )
    # result ∈ {"contradicts", "no_contradiction", "skipped_token_overlap",
    #          "skipped_budget", "skipped_llm_error"}
    result: Mapped[str] = mapped_column(String(30), nullable=False)
    skip_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class GraphEdge(Base):
    """Adjacency table for the context/pattern graph."""
    __tablename__ = "graph_edges"
    __table_args__ = (
        CheckConstraint("weight >= 0", name="ck_graph_edges_weight_nonnegative"),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_graph_edges_confidence_range",
        ),
        CheckConstraint(
            "valid_to IS NULL OR valid_to > valid_from",
            name="ck_graph_edges_valid_window",
        ),
        Index(
            "uq_graph_edges_active_logical",
            "tenant_id",
            "domain_id",
            "source_node_type",
            "source_node_id",
            "target_node_type",
            "target_node_id",
            "edge_type",
            unique=True,
            postgresql_where=text("valid_to IS NULL"),
            postgresql_nulls_not_distinct=True,
        ),
        Index(
            "ix_graph_edges_current_source",
            "tenant_id",
            "source_node_type",
            "source_node_id",
            "domain_id",
            postgresql_where=text("valid_to IS NULL"),
        ),
        Index(
            "ix_graph_edges_current_target",
            "tenant_id",
            "target_node_type",
            "target_node_id",
            "domain_id",
            postgresql_where=text("valid_to IS NULL"),
        ),
        Index(
            "ix_graph_edges_temporal_source",
            "tenant_id",
            "source_node_type",
            "source_node_id",
            "valid_from",
            "valid_to",
        ),
        Index(
            "ix_graph_edges_temporal_target",
            "tenant_id",
            "target_node_type",
            "target_node_id",
            "valid_from",
            "valid_to",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    domain_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("domains.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    source_node_type: Mapped[str] = mapped_column(String(50), nullable=False)
    source_node_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    target_node_type: Mapped[str] = mapped_column(String(50), nullable=False)
    target_node_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    edge_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    weight: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    metadata_extra: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # AE Ops Context Graph alignment — temporal validity (Section 43.1).
    # Enables "what was true at incident time?" queries instead of
    # always-current-state. Both nullable: existing rows continue to
    # behave as "valid since creation, no expiry".
    valid_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    valid_to: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Semantically distinct from ``weight`` (importance for traversal):
    # ``confidence`` is the belief in the relation. Nullable so existing
    # rows aren't forced to commit to a confidence value.
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
