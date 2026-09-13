import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from contextedge.models.base import Base, MspScopedMixin, TenantScopedMixin, TimestampMixin


class Msp(Base, TimestampMixin):
    """A managed service provider — the hard isolation boundary (plan D7).

    SupportFlo sells to MSPs; an MSP serves many clients. A client sees only
    itself; an MSP sees across its own clients and never across another's.
    That asymmetry is encoded in two database roles rather than assumed in
    application code (plan D8, migration ``0097``).

    The platform operator is deliberately NOT a third row here. It is a role
    (``platform_super_admin``) holding credentials that no policy admits,
    because a platform tier expressed as data would be one more id an
    application bug could set.
    """

    __tablename__ = "msps"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    # Residency is a per-MSP attribute so a later EU or enterprise deal needs
    # deployment work rather than a schema change (plan D6 tradeoff).
    data_residency: Mapped[str | None] = mapped_column(String(32), nullable=True)
    config: Mapped[dict] = mapped_column(JSONB, server_default="{}", nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)

    tenants: Mapped[list["Tenant"]] = relationship(back_populates="msp")


class Tenant(Base, TimestampMixin):
    """A client of an MSP. Named ``Tenant`` for continuity, not accuracy.

    ``tenants.msp_id`` is the tenant-to-MSP mapping D28 names. It is nullable
    only so the migration can land on a populated database; ``0097`` backfills
    it and the RLS policies treat NULL as no-access.
    """

    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    msp_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("msps.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    msp: Mapped["Msp | None"] = relationship(back_populates="tenants")
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    config: Mapped[dict] = mapped_column(JSONB, server_default="{}", nullable=False)
    sso_config: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    retention_defaults: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)

    workspaces: Mapped[list["Workspace"]] = relationship(back_populates="tenant")
    domains: Mapped[list["Domain"]] = relationship(back_populates="tenant")
    users: Mapped[list["User"]] = relationship(back_populates="tenant")


class Workspace(Base, TenantScopedMixin):
    __tablename__ = "workspaces"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    config: Mapped[dict] = mapped_column(JSONB, server_default="{}", nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)

    tenant: Mapped["Tenant"] = relationship(back_populates="workspaces")
    domains: Mapped[list["Domain"]] = relationship(back_populates="workspace")


class Domain(Base, TenantScopedMixin):
    __tablename__ = "domains"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)

    tenant: Mapped["Tenant"] = relationship(back_populates="domains")
    workspace: Mapped["Workspace | None"] = relationship(back_populates="domains")


class User(Base, TenantScopedMixin):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("tenant_id", "username", name="uq_users_tenant_username"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    username: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True, index=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)
    sso_provider: Mapped[str | None] = mapped_column(String(100), nullable=True)

    role_bindings: Mapped[list["RoleBinding"]] = relationship(back_populates="user")
    tenant: Mapped["Tenant"] = relationship(back_populates="users")


class RoleBinding(Base, TenantScopedMixin):
    __tablename__ = "role_bindings"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(50), nullable=False)
    scope_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="tenant"
    )
    scope_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )

    user: Mapped["User"] = relationship(back_populates="role_bindings")


class RoleNavAccess(Base, TimestampMixin):
    """Platform-wide mapping of role -> sidebar href. Edited by platform super admin."""

    __tablename__ = "role_nav_access"
    __table_args__ = (UniqueConstraint("role", "href", name="uq_role_nav_access_role_href"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    role: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    href: Mapped[str] = mapped_column(String(200), nullable=False)


# Valid values for ``TenantLLMBudget.action_on_exceed``. Matches the
# CHECK constraint on the column.
BUDGET_ACTIONS = ("block", "warn")


class TenantLLMBudget(Base, MspScopedMixin):
    """Per-tenant daily cap on LLM spend.

    Rows are optional — tenants without a row have no cap. See
    ``services/tenant_budget_service.py`` for enforcement semantics and
    ``ENTERPRISE_ARCHITECTURE_REVIEW.md`` §6 item 14 for motivation.
    """

    __tablename__ = "tenant_llm_budgets"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )
    daily_token_limit: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    daily_cost_cap_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    action_on_exceed: Mapped[str] = mapped_column(
        String(20), nullable=False, default="warn", server_default="warn",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
