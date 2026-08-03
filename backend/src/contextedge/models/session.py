import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from contextedge.models.base import Base, TenantScopedMixin


class ResolutionSession(Base, TenantScopedMixin):
    __tablename__ = "resolution_sessions"
    __table_args__ = (
        Index(
            "uq_resolution_sessions_tenant_case_number",
            "tenant_id",
            "case_number",
            unique=True,
            postgresql_where=text("case_number IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id"),
        nullable=False,
        index=True,
    )
    domain_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("domains.id"),
        nullable=True,
        index=True,
    )
    initiated_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="open", nullable=False, index=True)
    symptoms: Mapped[list] = mapped_column(JSONB, server_default="[]", nullable=False)
    entities: Mapped[list] = mapped_column(JSONB, server_default="[]", nullable=False)
    external_case_ids: Mapped[list] = mapped_column(JSONB, server_default="[]", nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # AE Ops Context Graph alignment — case spine columns. All nullable
    # for back-compat; populated by AE/SN ingestion or graduated from
    # ``entities[]`` JSONB during a case enrichment pass.
    case_number: Mapped[str | None] = mapped_column(
        String(60), nullable=True, index=True
    )
    case_type: Mapped[str | None] = mapped_column(String(60), nullable=True)
    issue_type: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    priority: Mapped[str | None] = mapped_column(String(20), nullable=True)
    severity: Mapped[str | None] = mapped_column(String(20), nullable=True)
    environment: Mapped[str | None] = mapped_column(String(30), nullable=True, index=True)

    # Structured FKs to entities for the four design-mandated dimensions.
    # SET NULL on entity delete preserves the case audit record.
    user_entity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("entities.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    workflow_entity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("entities.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    request_entity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("entities.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    agent_entity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("entities.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    trace_events: Mapped[list["DecisionTraceEvent"]] = relationship(
        back_populates="session",
        order_by="DecisionTraceEvent.created_at",
    )
    decisions: Mapped[list] = relationship(
        "contextedge.models.decision.Decision",
        foreign_keys="contextedge.models.decision.Decision.session_id",
        order_by="contextedge.models.decision.Decision.created_at",
        lazy="noload",
    )


class DecisionTraceEvent(Base):
    __tablename__ = "decision_trace_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("resolution_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id"),
        nullable=False,
        index=True,
    )
    # Optional decision anchor — when populated this row also serves as a
    # cg_decision_step (per the design Section 11.11) with tool I/O
    # references. Nullable to keep existing session-scoped trace events
    # working unchanged.
    decision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("decisions.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    tool_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    tool_input_ref: Mapped[str | None] = mapped_column(String(500), nullable=True)
    tool_output_ref: Mapped[str | None] = mapped_column(String(500), nullable=True)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    inputs: Mapped[dict] = mapped_column(JSONB, server_default="{}", nullable=False)
    outputs: Mapped[dict] = mapped_column(JSONB, server_default="{}", nullable=False)
    reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    session: Mapped["ResolutionSession"] = relationship(back_populates="trace_events")


class CaseLink(Base):
    __tablename__ = "case_links"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id"),
        nullable=False,
        index=True,
    )
    canonical_case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    system: Mapped[str] = mapped_column(String(100), nullable=False)
    external_id: Mapped[str] = mapped_column(String(500), nullable=False)
    evidence_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("evidence_items.id"),
        nullable=True,
    )
    confidence: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    first_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    last_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
