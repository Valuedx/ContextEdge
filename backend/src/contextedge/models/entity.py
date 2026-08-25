"""Operational-noun entity table.

Coexists with ``CanonicalIdentity`` (which remains the identity-resolution
spine for people/aliases). The ``entities`` table holds the AE Ops
operational nouns the design treats as first-class graph nodes:
``workflow``, ``workflow_request``, ``agent_machine``, ``schedule``,
``output_location``, plus ``application`` / ``database`` /
``file_share`` / ``business_service`` / ``incident`` / ``sop`` as the
graph extends.

Kept separate from ``canonical_identities`` deliberately:

- ``CanonicalIdentity`` is shaped around alias resolution and is
  referenced by ``IdentityAlias`` / ``EvidenceIdentityLink``. Adding
  workflow/agent rows there would mix two query patterns.
- ``entities`` follows the design's natural key
  ``(entity_type, external_system, external_id)`` so AE / ServiceNow
  ingestion can upsert idempotently.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from contextedge.models.base import Base, TenantScopedMixin

ENTITY_TYPES = (
    "user",
    "workflow",
    "workflow_request",
    "workflow_step",
    "agent_machine",
    "schedule",
    "output_location",
    "application",
    "database",
    "file_share",
    "business_service",
    "incident",
    "sop",
    "tool",
    "approval_group",
    # ServiceNow reference enrichment (servicenow_reference_service):
    # cmdb_ci rows whose class has no richer mapping, and the owning team.
    "configuration_item",
    "assignment_group",
    # Zoho Desk reference enrichment (zoho_desk_reference_service): the
    # customer account a ticket belongs to, the KB category an article
    # documents, and ticket tags. These were being WRITTEN without being
    # registered here — the exact ontology drift this tuple exists to
    # prevent, caught by an external review and now pinned by
    # test_entity_type_registry.py, which fails the moment any reference
    # service writes a type this registry does not know.
    "customer_account",
    "knowledge_category",
    "topic",
)


class Entity(Base, TenantScopedMixin):
    __tablename__ = "entities"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "entity_type",
            "external_system",
            "external_id",
            name="uq_entities_tenant_type_system_external_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    domain_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("domains.id"), nullable=True, index=True
    )

    entity_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    external_system: Mapped[str | None] = mapped_column(String(100), nullable=True)
    external_id: Mapped[str | None] = mapped_column(String(500), nullable=True)
    name: Mapped[str] = mapped_column(String(500), nullable=False)

    environment: Mapped[str | None] = mapped_column(String(30), nullable=True, index=True)
    # Normalized traits (0043): the deciding dimensions for fix
    # applicability. NULL means the source did not provide the trait —
    # absent is absent, never guessed.
    manufacturer: Mapped[str | None] = mapped_column(String(120), nullable=True)
    model: Mapped[str | None] = mapped_column(String(160), nullable=True)
    os_name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    os_version: Mapped[str | None] = mapped_column(String(80), nullable=True)
    business_unit: Mapped[str | None] = mapped_column(String(100), nullable=True)
    data_domain: Mapped[str | None] = mapped_column(String(100), nullable=True)

    attributes: Mapped[dict] = mapped_column(
        JSONB, server_default="{}", nullable=False
    )
    source_ref: Mapped[dict] = mapped_column(
        JSONB, server_default="{}", nullable=False
    )

    # 1.0 by default — entity rows that come from a deterministic source
    # (AE DB, ServiceNow CI) carry full confidence; LLM-inferred entities
    # start lower.
    confidence: Mapped[float] = mapped_column(
        Numeric(5, 4), default=1.0, nullable=False
    )

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
