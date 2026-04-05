import uuid
from typing import ClassVar

from sqlalchemy import Boolean, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from contextedge.models.base import Base, TenantScopedMixin

POLICY_TYPES: frozenset[str] = frozenset(
    {"retention", "classification", "access", "approval"}
)


class TenantPolicy(Base, TenantScopedMixin):
    """Tenant-scoped policy document (type + JSON config)."""

    __tablename__ = "tenant_policies"
    __table_args__ = (Index("ix_tenant_policies_tenant_type", "tenant_id", "policy_type"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )
    policy_type: Mapped[str] = mapped_column(String(30), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    config: Mapped[dict] = mapped_column(JSONB, server_default="{}", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    TYPE_TO_RESPONSE_KEY: ClassVar[dict[str, str]] = {
        "retention": "retention_policies",
        "classification": "classification_policies",
        "access": "access_policies",
        "approval": "approval_policies",
    }
