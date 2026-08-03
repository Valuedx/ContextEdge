"""Fleet-group suggestions (migration 0048, backlog B6)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from contextedge.models.base import Base


class FleetGroupSuggestion(Base):
    __tablename__ = "fleet_group_suggestions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "change_ref", name="uq_fleet_group_change"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )
    change_ref: Mapped[str] = mapped_column(String(120), nullable=False)
    change_evidence_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("evidence_items.id", ondelete="SET NULL"),
        nullable=True,
    )
    member_evidence_ids: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    member_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    parent_case_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    reviewed_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
