import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from contextedge.models.base import Base, TenantOwnedMixin, TenantScopedMixin

if TYPE_CHECKING:
    from contextedge.models.evidence import EvidenceItem

# Lifecycle of a canonical identity's resolution (migration 0033):
#   resolved     — matched/created before the layered resolver, or auto-linked
#   provisional  — created on an unmatched mention; usable but untrusted
#   needs_review — the adjudicator abstained or scored below threshold
#   verified     — a human confirmed the identity (review workflow)
RESOLUTION_STATES = ("resolved", "provisional", "needs_review", "verified")

# Alias types that identify uniquely per tenant (partial unique index
# uq_identity_aliases_tenant_strong, migration 0033). Display names are
# deliberately not in this set — two employees can share a name.
STRONG_ALIAS_TYPES = (
    "email",
    "username",
    "hostname",
    "fqdn",
    "ip_address",
    "serial_number",
    "external_id",
)


class CanonicalIdentity(Base, TenantScopedMixin):
    __tablename__ = "canonical_identities"
    # Mirrors migration 0033 so metadata-built schemas (tests, dev
    # bootstrap) match migration-built databases.
    __table_args__ = (
        Index(
            "ix_canonical_identities_tenant_type_normalized",
            "tenant_id",
            "entity_type",
            "normalized_name",
        ),
        Index(
            "ix_canonical_identities_resolution_state",
            "tenant_id",
            "resolution_state",
            postgresql_where=text(
                "resolution_state IN ('provisional', 'needs_review')"
            ),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    canonical_name: Mapped[str] = mapped_column(String(500), nullable=False, index=True)
    normalized_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    resolution_state: Mapped[str] = mapped_column(
        String(20), nullable=False, default="resolved", server_default="resolved"
    )
    resolution_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    resolution_method: Mapped[str | None] = mapped_column(String(50), nullable=True)
    metadata_extra: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)

    aliases: Mapped[list["IdentityAlias"]] = relationship(back_populates="canonical_identity")
    evidence_links: Mapped[list["EvidenceIdentityLink"]] = relationship(back_populates="identity")


class IdentityAlias(Base):
    __tablename__ = "identity_aliases"
    # Mirrors migration 0033 (strong-alias tenant uniqueness + typed lookup
    # index) so metadata-built schemas enforce the same constraints the
    # resolver's ON CONFLICT inserts rely on.
    __table_args__ = (
        Index(
            "ix_identity_aliases_tenant_type_normalized",
            "tenant_id",
            "alias_type",
            "normalized_alias",
        ),
        Index(
            "uq_identity_aliases_tenant_strong",
            "tenant_id",
            "alias_type",
            "normalized_alias",
            unique=True,
            postgresql_where=text(
                "alias_type IN ('email', 'username', 'hostname', 'fqdn', "
                "'ip_address', 'serial_number', 'external_id')"
            ),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    canonical_identity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("canonical_identities.id"),
        nullable=False,
        index=True,
    )
    # Denormalized from the canonical row (0033) so alias uniqueness can be
    # tenant-scoped without a join. Not index=True: the composite lookup
    # index above leads with tenant_id.
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    alias_text: Mapped[str] = mapped_column(String(500), nullable=False, index=True)
    normalized_alias: Mapped[str | None] = mapped_column(String(500), nullable=True)
    alias_type: Mapped[str] = mapped_column(
        String(30), nullable=False, default="display_name", server_default="display_name"
    )
    source_system: Mapped[str | None] = mapped_column(String(50), nullable=True)
    source_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    times_observed: Mapped[int] = mapped_column(
        Integer,
        default=1,
        server_default="1",
        nullable=False,
    )

    canonical_identity: Mapped["CanonicalIdentity"] = relationship(back_populates="aliases")


class EvidenceIdentityLink(Base):
    __tablename__ = "evidence_identity_links"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    evidence_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("evidence_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    identity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("canonical_identities.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    match_type: Mapped[str] = mapped_column(String(50), nullable=False, default="alias_match")
    confidence: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    source_metadata: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    evidence_item: Mapped["EvidenceItem"] = relationship(back_populates="identity_links")
    identity: Mapped["CanonicalIdentity"] = relationship(back_populates="evidence_links")


class CorrelationEdge(Base, TenantScopedMixin):
    __tablename__ = "correlation_edges"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_evidence_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("evidence_items.id", ondelete="CASCADE"),
        nullable=False,
    )
    target_evidence_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("evidence_items.id", ondelete="CASCADE"),
        nullable=False,
    )
    correlation_type: Mapped[str] = mapped_column(String(30), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)
    explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(50), nullable=True)


class Episode(Base, TenantScopedMixin):
    __tablename__ = "episodes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    domain_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("domains.id"),
        nullable=True,
    )
    primary_case_ref: Mapped[str | None] = mapped_column(String(500), nullable=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="draft", nullable=False, index=True)
    extraction_confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    root_cause_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    final_outcome: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewer_state: Mapped[str] = mapped_column(
        String(30),
        default="pending_review",
        nullable=False,
    )
    reviewer_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    evidence_ids: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    # Hash of the resolved cluster's evidence set (0037): powers draft
    # idempotency and supersede-on-growth in episode reconstruction.
    cluster_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    entity_refs: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Conflicting accounts between sources, preserved instead of merged
    # (0040): [{"topic": ..., "accounts": [{"evidence_id": ..., "claim": ...}]}]
    contradictions: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    embedding = mapped_column(Vector(3072), nullable=True)
    # What generated this episode (0055): prompt name/version, routing task,
    # requested model, and the correlation id that joins to the llm.usage
    # events carrying the SERVING model. NULL means unknown — never "the
    # current default", which is what re-deriving it at read time would claim.
    generation_provenance: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # The AI reviewer's assessment (0070): verdict, confidence, reasons,
    # prompt version, reviewed_at, and — when the sweep approved — the
    # deterministic floors it passed. NULL means never AI-reviewed; the
    # sweep uses that to avoid paying twice for the same draft. An
    # auto-approval is permanently distinguishable from a human one:
    # reviewer_user_id stays NULL and this field says why.
    ai_review: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    steps: Mapped[list["EpisodeStep"]] = relationship(
        back_populates="episode",
        order_by="EpisodeStep.step_order",
    )


class EpisodeStep(Base, TenantOwnedMixin):
    __tablename__ = "episode_steps"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    episode_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("episodes.id"),
        nullable=False,
        index=True,
    )
    step_order: Mapped[int] = mapped_column(Integer, nullable=False)
    step_type: Mapped[str] = mapped_column(String(30), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    observation: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_state: Mapped[str] = mapped_column(String(30), default="unknown", nullable=False)
    failed_flag: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    successful_flag: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    evidence_refs: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    extraction_confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    episode: Mapped["Episode"] = relationship(back_populates="steps")


class EpisodeEvidenceLink(Base):
    """Normalized episode↔evidence membership (migration 0037).

    The JSONB ``Episode.evidence_ids`` list remains for cheap reads; this
    table is the queryable provenance — which evidence grounds which
    episode, and why (``link_reason``: the cluster reason or
    ``model_attribution`` when the extractor assigned it).
    """

    __tablename__ = "episode_evidence_links"
    __table_args__ = (
        UniqueConstraint("episode_id", "evidence_id", name="uq_episode_evidence"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    episode_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("episodes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    evidence_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("evidence_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    link_reason: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class IdentityMergeProposal(Base, TenantScopedMixin):
    """A pair of identities a reconciliation pass believes are the same.

    Per-mention adjudication cannot produce these. It only sees
    candidates sharing a substring with the incoming name, so "SFA" and
    "Sales Force Automation" were never presented together and forked
    into two rows — as did "HP UPD" and "HP Universal Print Driver". A
    pass reading the whole provisional set at once sees both.

    Proposed, never applied: merging re-points aliases and deactivates an
    identity, which is not something to do on a model's word alone.

    Stored rather than recomputed so a reviewer's rejection is durable —
    without it a scheduled run re-raises every rejected pair forever.
    """

    __tablename__ = "identity_merge_proposals"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "primary_identity_id",
            "duplicate_identity_id",
            name="uq_identity_merge_proposal_pair",
        ),
        Index(
            "ix_identity_merge_proposals_pending",
            "tenant_id",
            "status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # The identity to KEEP, and the one folded into it.
    primary_identity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("canonical_identities.id", ondelete="CASCADE"),
        nullable=False,
    )
    duplicate_identity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("canonical_identities.id", ondelete="CASCADE"),
        nullable=False,
    )
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # pending | accepted | rejected
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    proposed_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
