import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, Float, ForeignKey, Integer, LargeBinary, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from contextedge.models.base import Base, TenantScopedMixin


class RawEvidenceObject(Base, TenantScopedMixin):
    __tablename__ = "raw_evidence_objects"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("sources.id"), nullable=False, index=True)
    source_object_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("source_objects.id"), nullable=True)
    external_id: Mapped[str] = mapped_column(String(500), nullable=False, index=True)
    raw_payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    stored_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    object_storage_key: Mapped[str | None] = mapped_column(String(500), nullable=True)


class EvidenceItem(Base, TenantScopedMixin):
    __tablename__ = "evidence_items"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    domain_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("sources.id"), nullable=False, index=True)
    source_object_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    thread_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("threads.id"), nullable=True)
    raw_object_ref: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    evidence_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    body_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    body_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at_source: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    relevance_state: Mapped[str] = mapped_column(String(30), default="unclassified", nullable=False)
    relevance_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    sensitivity_label: Mapped[str | None] = mapped_column(String(50), nullable=True)
    access_policy_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenant_policies.id", ondelete="SET NULL"),
        nullable=True,
    )
    canonical_entity_refs: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    baseline_ref: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    delta_signal: Mapped[str | None] = mapped_column(String(20), nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    embedding = mapped_column(Vector(3072), nullable=True)

    # AE Ops Context Graph alignment.
    # ``evidence_time`` is the time the *evidence subject* occurred — a
    # log line at 10:42 vs the source object created at 10:45. Distinct
    # from ``created_at_source`` (record creation) and ``ingested_at``.
    evidence_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    # Agent or human that captured the evidence (for SoD / lineage).
    collected_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # Controlled vocab e.g. AE_API | AE_AGENT_LOG | SERVICENOW | TEAMS |
    # EMAIL | SOP | MONITORING | HUMAN_NOTE — kept free text for
    # forward-compat but indexed for filter queries.
    source_type: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    redaction_status: Mapped[str | None] = mapped_column(String(30), nullable=True)

    search_tsvector: Mapped[object | None] = mapped_column(
        TSVECTOR,
        server_default=func.now(),  # Placeholder to indicate server-side management
        deferred=True,
    )

    thread: Mapped["Thread | None"] = relationship(back_populates="evidence_items")
    attachments: Mapped[list["AttachmentArtifact"]] = relationship(back_populates="evidence_item")
    identity_links: Mapped[list["EvidenceIdentityLink"]] = relationship(back_populates="evidence_item")
    chunks: Mapped[list["EvidenceChunk"]] = relationship(
        back_populates="evidence_item",
        cascade="all, delete-orphan",
    )

    # Stamped by ``services.evidence_chunk_service.write_chunks`` once a
    # chunker run lands. NULL means not-yet-chunked — used by the
    # backfill scanner. ``chunk_count`` is observability-only.
    chunked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    chunk_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class EvidenceChunk(Base, TenantScopedMixin):
    """High-recall sibling of ``EvidenceItem``.

    One row per chunk produced by the source-specific chunker. Vector
    search hits this table; results aggregate to ``evidence_id`` for the
    card surface (max chunk score per evidence). The parent's own
    ``embedding`` column is preserved unchanged so contradiction
    scanning, similar-decision retrieval, and baseline matching keep
    working without modification.

    The ``(evidence_id, chunk_index, chunker_version)`` unique key lets
    a re-chunk write the new version alongside the old one. Atomic swap
    is just updating ``EvidenceItem.chunked_at`` to the new run; legacy
    rows are GC'd by a maintenance task.
    """

    __tablename__ = "evidence_chunks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    evidence_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("evidence_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    # Controlled vocab: 'body' | 'comment' | 'message' | 'log_event' |
    # 'heading_section' | 'code_block' | 'ocr_text'. Free-text for
    # forward compat, indexed for filter queries.
    chunk_kind: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    char_offset_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    char_offset_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Heading breadcrumb for hierarchical chunkers, e.g.
    # "Postmortem > Timeline > 14:32".
    parent_section: Mapped[str | None] = mapped_column(Text, nullable=True)
    embedding = mapped_column(Vector(3072), nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    chunk_metadata: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    chunker_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    evidence_item: Mapped["EvidenceItem"] = relationship(back_populates="chunks")


class Thread(Base, TenantScopedMixin):
    __tablename__ = "threads"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("sources.id"), nullable=False)
    source_object_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    external_thread_id: Mapped[str] = mapped_column(String(500), nullable=False, index=True)
    title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    participant_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    message_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    first_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    hydration_status: Mapped[str] = mapped_column(String(30), default="pending", nullable=False)
    relevance_state: Mapped[str] = mapped_column(String(30), default="unclassified", nullable=False)

    evidence_items: Mapped[list["EvidenceItem"]] = relationship(back_populates="thread")


class AttachmentArtifact(Base):
    __tablename__ = "attachment_artifacts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    evidence_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("evidence_items.id", ondelete="CASCADE"), nullable=False)
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    object_storage_key: Mapped[str] = mapped_column(String(500), nullable=False)
    extracted_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    extraction_status: Mapped[str] = mapped_column(String(30), default="pending", nullable=False)
    parser_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    parser_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    extraction_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    parser_metadata: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    extracted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    evidence_item: Mapped["EvidenceItem"] = relationship(back_populates="attachments")
