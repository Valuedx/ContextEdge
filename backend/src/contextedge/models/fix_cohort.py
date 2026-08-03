"""Per-cohort fix outcome counters (migration 0047, backlog B5)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from contextedge.models.base import Base

COHORT_TYPES = ("model", "class", "family")


class FixCohortStat(Base):
    __tablename__ = "fix_cohort_stats"
    __table_args__ = (
        UniqueConstraint(
            "fix_pattern_id", "cohort_type", "cohort_key", name="uq_fix_cohort"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )
    fix_pattern_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("fix_patterns.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    cohort_type: Mapped[str] = mapped_column(String(20), nullable=False)
    cohort_key: Mapped[str] = mapped_column(String(160), nullable=False)
    success_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failure_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
