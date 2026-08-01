"""Gated semantic correlation suggestions (migration 0039).

Semantic similarity alone never creates correlation edges — suggestions
sit in this table until a reviewer accepts (creating an ordinary
CorrelationEdge) or rejects (remembered; the pair is never
re-suggested). The pair is stored normalized (smaller UUID first) so
the symmetric duplicate cannot exist.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from contextedge.models.base import Base

SUGGESTION_STATUSES = ("pending", "accepted", "rejected")


class CorrelationSuggestion(Base):
    __tablename__ = "correlation_suggestions"
    __table_args__ = (
        UniqueConstraint(
            "evidence_id_low", "evidence_id_high", name="uq_correlation_suggestion_pair"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )
    evidence_id_low: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("evidence_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    evidence_id_high: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("evidence_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    similarity: Mapped[float] = mapped_column(Float, nullable=False)
    corroborators: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    reviewed_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
