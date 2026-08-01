"""Thread-topic state (migration 0044, backlog A3).

One current topic per thread (unique thread_id); history lives in
``thread.topic_changed`` operational events, not extra rows. A
provisional topic marks a thread discussing an incident that has no
ticket yet — no memberships are written under it; it exists so the
anchor's arrival can unify the thread retroactively.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from contextedge.models.base import Base

TOPIC_SET_BY = ("anchor", "correction", "thread_seed")


class ThreadTopic(Base):
    __tablename__ = "thread_topics"
    __table_args__ = (
        UniqueConstraint("thread_id", name="uq_thread_topic"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )
    thread_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("threads.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    canonical_case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    is_provisional: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    set_by: Mapped[str] = mapped_column(String(30), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.75, nullable=False)
    since: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
