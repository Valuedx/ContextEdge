"""Entity class taxonomy (migration 0042, backlog B1).

A global (not tenant-scoped) single-parent tree of CI classes — the
generalization ladder for fix applicability. Instances connect to
classes via ``instance_of`` graph edges; classes to parents via
``subclass_of`` edges (materialized per tenant on first use so graph
traversal never needs this table at query time).

OS is deliberately NOT a class (a laptop is both portable and
Windows); it is a normalized trait on the entity (B2).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from contextedge.models.base import Base


class EntityClass(Base):
    __tablename__ = "entity_classes"
    __table_args__ = (
        UniqueConstraint("canonical_key", name="uq_entity_classes_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    canonical_key: Mapped[str] = mapped_column(String(80), nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    parent_class_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("entity_classes.id"), nullable=True
    )
    class_family: Mapped[str] = mapped_column(
        String(50), default="general", nullable=False
    )
    attributes_schema: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
