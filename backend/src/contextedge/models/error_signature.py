"""Normalised error signature + statistical fix pattern.

These two tables are deliberately separate from ``Pattern`` and
``Playbook``:

- ``Pattern`` is a high-level "there's a recurring issue here" model;
  ``error_signatures`` is a low-level "this exact log shape" fingerprint.
  A Pattern can aggregate multiple signatures.
- ``Playbook``/``PlaybookVersion`` is an executable runbook; ``fix_patterns``
  is the recommender's answer to "what's known to work for this issue?",
  with success/failure counters that decay confidence over time.

Counters on both tables are intended to be incremented from the case
outcome write path (case_outcomes.successful_action increments the
matching fix_pattern; failed_actions[] increment failure counts).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from contextedge.models.base import Base, TenantScopedMixin


class ErrorSignature(Base, TenantScopedMixin):
    __tablename__ = "error_signatures"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )
    domain_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("domains.id"), nullable=True, index=True
    )

    # Stable normalised key like ``SMTP_TIMEOUT_AFTER_OUTPUT_GENERATED``.
    # Tenant-scoped uniqueness — different tenants may classify the same
    # error differently.
    signature_key: Mapped[str] = mapped_column(
        String(120), nullable=False, index=True
    )
    display_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    error_type: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    normalized_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    patterns: Mapped[list] = mapped_column(
        JSONB, server_default="[]", nullable=False
    )
    example_messages: Mapped[list] = mapped_column(
        JSONB, server_default="[]", nullable=False
    )

    usual_causes: Mapped[list] = mapped_column(
        JSONB, server_default="[]", nullable=False
    )
    recommended_actions: Mapped[list] = mapped_column(
        JSONB, server_default="[]", nullable=False
    )
    risk_notes: Mapped[list] = mapped_column(
        JSONB, server_default="[]", nullable=False
    )

    success_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failure_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    confidence: Mapped[float] = mapped_column(
        Numeric(5, 4), default=0.5, nullable=False
    )

    # Optional bridge to the higher-level Pattern aggregation.
    pattern_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("patterns.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class FixPattern(Base, TenantScopedMixin):
    """Statistical record of "this fix is known to work for this issue".

    Distinct from ``Playbook``: a FixPattern carries the recommender
    metadata (issue_type, success/failure counts, source cases) and
    optionally points to an executable Playbook. Multiple FixPatterns
    can share the same Playbook with different precondition contexts.
    """

    __tablename__ = "fix_patterns"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )
    domain_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("domains.id"), nullable=True, index=True
    )

    pattern_name: Mapped[str] = mapped_column(String(255), nullable=False)
    issue_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)

    workflow_entity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("entities.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    error_signature_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("error_signatures.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    failed_step: Mapped[str | None] = mapped_column(String(255), nullable=True)

    recommended_fix: Mapped[str] = mapped_column(Text, nullable=False)
    recommended_playbook_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("playbooks.id", ondelete="SET NULL"),
        nullable=True,
    )
    preconditions: Mapped[list] = mapped_column(
        JSONB, server_default="[]", nullable=False
    )

    risk_level: Mapped[str | None] = mapped_column(String(20), nullable=True)
    approval_required: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )

    source_case_ids: Mapped[list] = mapped_column(
        JSONB, server_default="[]", nullable=False
    )

    success_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failure_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    confidence: Mapped[float] = mapped_column(
        Numeric(5, 4), default=0.5, nullable=False, index=True
    )

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
