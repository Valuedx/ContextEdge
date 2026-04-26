"""Action-keyed policy model.

Distinct from ``TenantPolicy`` (which stays as a generic config bucket
for retention/classification/access/approval). This table is queried by
``(tenant_id, action_name, workflow_entity_id NULL, environment NULL)``
and returns a verdict ``policy_result`` the executor honours:

    allowed_auto | approval_required | recommendation_only |
    restricted | manual_only

Precedence (priority + scope + conflict_resolution) and overrides are
on the design roadmap (Section 43.12) but out of scope for this initial
landing — the columns are provisioned but the engine is added later.
"""

from __future__ import annotations

import uuid

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from contextedge.models.base import Base, TenantScopedMixin


RISK_LEVELS = ("low", "medium", "high", "restricted")
POLICY_RESULTS = (
    "allowed_auto",
    "approval_required",
    "recommendation_only",
    "restricted",
    "manual_only",
)
EXECUTION_MODES = (
    "auto",
    "human_approved_auto",
    "human_approved_only",
    "manual",
    "recommendation_only",
)


class ActionPolicy(Base, TenantScopedMixin):
    __tablename__ = "action_policies"
    __table_args__ = (
        Index(
            "ix_action_policies_lookup",
            "tenant_id",
            "action_name",
            "workflow_entity_id",
            "environment",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )

    policy_name: Mapped[str] = mapped_column(String(255), nullable=False)
    action_name: Mapped[str] = mapped_column(String(120), nullable=False, index=True)

    workflow_entity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("entities.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    environment: Mapped[str | None] = mapped_column(String(30), nullable=True)
    business_unit: Mapped[str | None] = mapped_column(String(100), nullable=True)
    data_domain: Mapped[str | None] = mapped_column(String(100), nullable=True)

    risk_level: Mapped[str] = mapped_column(String(20), nullable=False)
    policy_result: Mapped[str] = mapped_column(String(40), nullable=False, index=True)

    required_approver_roles: Mapped[list] = mapped_column(
        JSONB, server_default="[]", nullable=False
    )
    allowed_execution_mode: Mapped[str | None] = mapped_column(
        String(40), nullable=True
    )

    conditions: Mapped[dict] = mapped_column(
        JSONB, server_default="{}", nullable=False
    )
    restrictions: Mapped[dict] = mapped_column(
        JSONB, server_default="{}", nullable=False
    )

    # Precedence groundwork for Section 43.12. Engine TBD.
    priority: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    policy_scope: Mapped[str | None] = mapped_column(String(40), nullable=True)
    conflict_resolution: Mapped[str] = mapped_column(
        String(40), default="most_restrictive", nullable=False
    )

    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
