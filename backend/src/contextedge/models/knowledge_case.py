"""Knowledge cases: what a source SAYS resolves something.

An `Episode` is an account of something that happened. A `KnowledgeCase`
is an account of what a curated source claims happens or works. Both carry
the same reconstructed semantics — symptoms, causes, actions, entities,
applicability — and the reconstruction of a KB article is genuinely
valuable: it is often the only structured description of a failure mode
nobody has hit yet.

What differs is the truth claim, and that is why this is a separate table
rather than a discriminator column on `episodes`. A `kind` column would
mean every existing and future query that counts, clusters, scores,
reviews, or cites episodes needs `AND kind = 'observed'` to stay correct,
and one forgotten predicate silently reintroduces exactly the
contamination this exists to prevent: a document's claim counted as an
observed outcome. A separate table makes that failure a missing join
rather than a wrong number.

**A KnowledgeCase never carries empirical confidence.** How well a
documented resolution actually works is a property of the pattern it
supports, measured from episodes, and recorded on `PatternEvidence`.
Storing it here would re-blur the provenance the split exists to keep.
KC-17 stays permanently "documentation said this"; it is the *pattern*
that graduates from documented-only to empirically supported as real
incidents arrive.
"""

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from contextedge.models.base import Base, TenantOwnedMixin, TenantScopedMixin


class KnowledgeCase(Base, TenantScopedMixin):
    """A documented resolution reconstructed from one knowledge source."""

    __tablename__ = "knowledge_cases"

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

    # --- provenance: which source said it, and with what authority --------
    source_evidence_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("evidence_items.id"), nullable=False, index=True
    )
    # kb_article | sop | vendor_advisory | runbook ... — the *kind* of source,
    # which is what decides whether this is documented or prescriptive.
    source_kind: Mapped[str] = mapped_column(
        String(40), default="kb_article", nullable=False
    )
    # Who stands behind it: internal_kb, vendor, community, unknown. A vendor
    # advisory and a community post are both "documented" and are not equally
    # trustworthy.
    source_authority: Mapped[str] = mapped_column(
        String(40), default="internal_kb", nullable=False
    )
    # The source's own lifecycle, not ours: an article retired upstream stops
    # being guidance even though the case row remains for history.
    source_state: Mapped[str | None] = mapped_column(String(40), nullable=True)
    source_published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    source_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # --- the reconstructed semantics --------------------------------------
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    symptom_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Deliberately "documented_cause", not "root_cause": the source asserts
    # it, nobody confirmed it here.
    documented_cause: Mapped[str | None] = mapped_column(Text, nullable=True)
    documented_resolution: Mapped[str | None] = mapped_column(Text, nullable=True)
    validation_guidance: Mapped[str | None] = mapped_column(Text, nullable=True)
    entity_refs: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Same shape as evidence_items.applicability, carried here so pattern
    # scoring can ask "does this documented resolution apply to this estate"
    # without re-reading the article.
    applicability: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    extraction_confidence: Mapped[float] = mapped_column(
        Float, default=0.0, nullable=False
    )
    embedding = mapped_column(Vector(3072), nullable=True)
    generation_provenance: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Set when this row was created by migrating an episode that should never
    # have been one. Keeps the old id reachable without keeping the old row
    # live: see migration 0072.
    migrated_from_episode_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default="now()", nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default="now()", nullable=False
    )

    steps: Mapped[list["KnowledgeCaseStep"]] = relationship(
        back_populates="knowledge_case", cascade="all, delete-orphan"
    )

    __table_args__ = (
        # One case per source document. A KB article reconstructed twice is a
        # duplicate, not a second opinion.
        Index(
            "uq_knowledge_case_source",
            "tenant_id",
            "source_evidence_id",
            unique=True,
        ),
    )


class KnowledgeCaseStep(Base, TenantOwnedMixin):
    """One documented action. Mirrors EpisodeStep's shape deliberately.

    The fields an episode step carries about what HAPPENED — failed_flag,
    successful_flag, result_state — are absent here on purpose. A document
    describes an action to take; it does not report that the action was
    taken or that it worked. Adding an outcome field to this table is how
    the distinction would erode.
    """

    __tablename__ = "knowledge_case_steps"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    knowledge_case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("knowledge_cases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    step_order: Mapped[int] = mapped_column(nullable=False)
    # diagnostic | action | check | branch | escalation
    step_type: Mapped[str] = mapped_column(String(40), default="action", nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    expected_outcome: Mapped[str | None] = mapped_column(Text, nullable=True)
    extraction_confidence: Mapped[float] = mapped_column(
        Float, default=0.5, nullable=False
    )
    evidence_refs: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default="now()", nullable=False
    )

    knowledge_case: Mapped["KnowledgeCase"] = relationship(back_populates="steps")

    __table_args__ = (
        Index(
            "uq_knowledge_case_step_order",
            "knowledge_case_id",
            "step_order",
            unique=True,
        ),
    )
