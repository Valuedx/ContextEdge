import uuid
from datetime import datetime
from typing import ClassVar

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from contextedge.models.base import Base, TenantScopedMixin

POLICY_TYPES: frozenset[str] = frozenset(
    {"retention", "classification", "access", "approval"}
)

# A check either passed, blocked the action, or did not apply. Three values,
# because each maps to a distinct thing the executor does next — there is no
# "warning" state here, since a policy that warns has already allowed.
POLICY_CHECK_RESULTS: tuple[str, ...] = ("pass", "fail", "not_applicable")


class TenantPolicy(Base, TenantScopedMixin):
    """Tenant-scoped policy document (type + JSON config)."""

    __tablename__ = "tenant_policies"
    __table_args__ = (Index("ix_tenant_policies_tenant_type", "tenant_id", "policy_type"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    policy_type: Mapped[str] = mapped_column(String(30), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    config: Mapped[dict] = mapped_column(JSONB, server_default="{}", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Bumped on every config change (0056). Without it, editing a policy
    # silently rewrote the rules every past decision had been judged under —
    # ``policy_checks`` keys on the version so that history stays fixed.
    version: Mapped[int] = mapped_column(
        Integer, default=1, server_default="1", nullable=False
    )
    effective_from: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    effective_to: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    TYPE_TO_RESPONSE_KEY: ClassVar[dict[str, str]] = {
        "retention": "retention_policies",
        "classification": "classification_policies",
        "access": "access_policies",
        "approval": "approval_policies",
    }


class PolicyCheck(Base):
    """One evaluation of one policy version against one artifact (0056).

    Append-only by convention. Keyed to the policy VERSION rather than to the
    policy row, so a later edit cannot rewrite the history of what a decision
    was judged under — which is the whole reason the version column exists.

    ``policy_id`` is ``ON DELETE SET NULL`` rather than CASCADE: "this run was
    evaluated against a policy that has since been deleted" is a real audit
    record, and losing it would be worse than keeping the orphan.
    """

    __tablename__ = "policy_checks"
    __table_args__ = (
        CheckConstraint(
            "result IN ('pass', 'fail', 'not_applicable')",
            name="ck_policy_checks_result",
        ),
        Index(
            "ix_policy_checks_tenant_entity",
            "tenant_id",
            "evaluated_entity_type",
            "evaluated_entity_id",
        ),
        Index("ix_policy_checks_tenant_evaluated_at", "tenant_id", "evaluated_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    policy_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenant_policies.id", ondelete="SET NULL"),
        nullable=True,
    )
    policy_type: Mapped[str] = mapped_column(String(30), nullable=False)
    policy_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Which rule inside the policy ran — e.g. ``max_automation_mode``,
    # ``forbid_self_approval``. One policy can gate several distinct things,
    # and "the approval policy passed" is not a useful audit answer.
    check_name: Mapped[str] = mapped_column(String(60), nullable=False)
    evaluated_entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    evaluated_entity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    result: Mapped[str] = mapped_column(String(20), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # What the check actually SAW. Without it a recorded verdict is
    # unreproducible: the inputs move, and "it passed" stops being checkable.
    input_snapshot: Mapped[dict] = mapped_column(
        JSONB, server_default="{}", nullable=False
    )
    evaluated_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    evaluated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
