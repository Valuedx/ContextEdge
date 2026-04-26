import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from contextedge.models.base import Base, TenantScopedMixin


class Pattern(Base, TenantScopedMixin):
    __tablename__ = "patterns"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    domain_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("domains.id"), nullable=True)
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

    evidence_links: Mapped[list["PatternEvidenceLink"]] = relationship(back_populates="pattern")


class PatternEvidenceLink(Base):
    __tablename__ = "pattern_evidence_links"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    pattern_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("patterns.id"), nullable=False, index=True)
    episode_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("episodes.id"), nullable=True)
    evidence_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    link_type: Mapped[str] = mapped_column(String(50), nullable=False)
    weight: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    pattern: Mapped["Pattern"] = relationship(back_populates="evidence_links")


class NegativeKnowledgeItem(Base, TenantScopedMixin):
    __tablename__ = "negative_knowledge_items"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    domain_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    step_text: Mapped[str] = mapped_column(Text, nullable=False)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="ineffective", nullable=False)
    evidence_refs: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(50), nullable=True)


class Contradiction(Base, TenantScopedMixin):
    __tablename__ = "contradictions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
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
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
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

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    domain_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    source_node_type: Mapped[str] = mapped_column(String(50), nullable=False)
    source_node_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    target_node_type: Mapped[str] = mapped_column(String(50), nullable=False)
    target_node_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    edge_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    weight: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    metadata_extra: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    # AE Ops Context Graph alignment — temporal validity (Section 43.1).
    # Enables "what was true at incident time?" queries instead of
    # always-current-state. Both nullable: existing rows continue to
    # behave as "valid since creation, no expiry".
    valid_from: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    valid_to: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Semantically distinct from ``weight`` (importance for traversal):
    # ``confidence`` is the belief in the relation. Nullable so existing
    # rows aren't forced to commit to a confidence value.
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
