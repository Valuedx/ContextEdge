"""Reviewer-gated knowledge supersession (F4b).

``services/documents/versioning.py`` can already tell that "VPN SOP v2.docx"
supersedes "VPN SOP.docx". Its own docstring names the gap it does not close:
retrieval "returns superseded guidance and nothing marks it as superseded".

The finding is a *proposal*, never an action. A filename is not grounds for
retiring an SOP — "Final" and "v2" are written by people in a hurry, folders
get reorganised, and the wrong call silently removes the only guidance that
exists for a problem. So this follows the ``IdentityMergeProposal`` pattern
exactly: stored rather than applied, decided by a human, and **rejection is
durable** — without persisting the rejection, a scheduled pass re-raises every
declined pair forever, which is how a review queue becomes noise nobody reads.

Accepting one writes a ``superseded_by`` edge between the two knowledge
evidence rows. The edge is what retrieval reads; this table is why it exists.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from contextedge.models.base import Base, TenantScopedMixin

SUPERSESSION_STATUSES = ("pending", "accepted", "rejected")


class KnowledgeSupersessionProposal(Base, TenantScopedMixin):
    __tablename__ = "knowledge_supersession_proposals"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "predecessor_evidence_id",
            "successor_evidence_id",
            name="uq_knowledge_supersession_pair",
        ),
        CheckConstraint(
            "status IN ('pending', 'accepted', 'rejected')",
            name="ck_knowledge_supersession_status",
        ),
        CheckConstraint(
            "predecessor_evidence_id <> successor_evidence_id",
            name="ck_knowledge_supersession_distinct",
        ),
        Index("ix_knowledge_supersession_pending", "tenant_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    # The article being replaced, and the one replacing it.
    predecessor_evidence_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("evidence_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    successor_evidence_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("evidence_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # The filename family both share, e.g. "vpn sop". Stored so a reviewer can
    # see the grouping that produced the pair without recomputing it.
    document_family: Mapped[str] = mapped_column(String(300), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    # WHY the heuristic thinks so — parsed versions, qualifier ranks, the
    # filenames themselves. A proposal a reviewer cannot audit is a proposal
    # they will either rubber-stamp or ignore.
    # Python-side defaults as well as server ones, deliberately: a proposal is
    # returned to the reviewer in the same request that creates it, and after
    # an async flush a server-default column is expired — reading it would
    # emit IO from attribute access (MissingGreenlet) instead of a value.
    signals: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default="{}", nullable=False
    )
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    proposed_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
        nullable=False,
    )
