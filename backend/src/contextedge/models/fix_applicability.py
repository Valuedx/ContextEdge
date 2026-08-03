"""Fix applicability rules (migration 0046, backlog B4).

``target_class_key`` references the taxonomy by canonical key (the
taxonomy is global and seeded; a soft key keeps rules portable across
environments). ``required_traits`` / ``excluded_traits`` are flat
{trait: value} predicates — the deterministic assessor validates every
required trait against the target before recommending.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from contextedge.models.base import Base

APPLICABILITY_LEVELS = (
    "exact_ci",
    "same_model_and_configuration",
    "same_component_or_version",
    "same_ci_class",
    "related_ci_class",
    "cross_class_capability",
    "semantic_only",
)


class FixApplicabilityRule(Base):
    __tablename__ = "fix_applicability_rules"

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
    target_class_key: Mapped[str | None] = mapped_column(String(80), nullable=True)
    required_traits: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    excluded_traits: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    applicability_level: Mapped[str] = mapped_column(String(40), nullable=False)
    minimum_evidence: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)
    approval_requirement: Mapped[str] = mapped_column(
        String(20), default="review", nullable=False
    )
    created_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
