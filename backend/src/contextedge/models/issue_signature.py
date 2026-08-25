"""Issue signatures (migration 0045, backlog B3).

The structured problem fingerprint: broader than an ErrorSignature's
exact error shape, narrower than embedding similarity. Extracted per
APPROVED episode, deduped per tenant on ``signature_key`` — episodes
sharing a signature form a recurrence chain (similar problems, never
the same occurrence).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from contextedge.models.base import Base


class IssueSignature(Base):
    __tablename__ = "issue_signatures"
    __table_args__ = (
        UniqueConstraint("tenant_id", "signature_key", name="uq_issue_signature_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    signature_key: Mapped[str] = mapped_column(String(240), nullable=False)
    affected_capability: Mapped[str] = mapped_column(String(80), nullable=False)
    failing_component: Mapped[str | None] = mapped_column(String(120), nullable=True)
    failure_mode: Mapped[str] = mapped_column(String(120), nullable=False)
    trigger_change: Mapped[str | None] = mapped_column(String(200), nullable=True)
    environment: Mapped[str | None] = mapped_column(String(50), nullable=True)
    scope: Mapped[str | None] = mapped_column(String(50), nullable=True)
    error_signature_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("error_signatures.id", ondelete="SET NULL"),
        nullable=True,
    )
    episode_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class EpisodeIssueSignature(Base):
    __tablename__ = "episode_issue_signatures"
    __table_args__ = (
        UniqueConstraint(
            "episode_id", "issue_signature_id", name="uq_episode_issue_signature"
        ),
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
    issue_signature_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("issue_signatures.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    confidence: Mapped[float] = mapped_column(Float, default=0.7, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
