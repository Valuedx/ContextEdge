import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class MspScopedMixin:
    """The MSP half of the two-level key (SupportFlo plan D7/D28).

    ``msp_id`` is denormalised onto every scoped row rather than reached by
    joining ``tenants``. A join inside a security predicate is both a
    performance trap and an extra surface for a mistake, so the key the
    policy tests lives on the row the policy protects.

    Nullable on purpose, for exactly one reason: existing rows predate the
    MSP dimension and a NOT NULL column cannot be added to a populated table
    without a default, and there is no correct default for "which MSP owns
    this". ``0097`` backfills from ``tenants.msp_id`` and the policies treat
    NULL as no-access, so an unbackfilled row is invisible rather than
    universally visible.
    """

    msp_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("msps.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )


class TenantOwnedMixin(MspScopedMixin):
    """Denormalized tenant_id on child rows (enforced by FK + RLS).

    ``tenant_id`` is the CLIENT half of the key. The name is kept because
    renaming it would rewrite 624 predicates in one commit with no way to
    review the result; the meaning is unchanged, and the MSP dimension is
    added above it rather than reinterpreting it.
    """

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )


class TenantScopedMixin(TimestampMixin, MspScopedMixin):
    """Every tenant-scoped table must include tenant_id (FK + RLS).

    See ``TenantOwnedMixin`` on why ``tenant_id`` still means the client.
    """

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
