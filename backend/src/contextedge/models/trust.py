"""Trust as a scoped, measured thing (F10).

Autonomy today is a mode on the playbook: ``supervised``, ``full_auto``. That
is a *configuration*, not a track record. It cannot answer the question that
should actually gate an autonomous action — has **this agent** done **this
action** on **this class of thing** in **this environment**, and did it hold?

A profile is one scope's record. The scope is the composite key, deliberately
wide: the same agent restarting a Windows service on a dev endpoint and failing
over an Oracle primary in production are not the same track record, and one
global number lets the easy case vouch for the hard one.

**The lower bound, not the rate.** Three successes out of three is a rate of
1.0 and means almost nothing; 340 out of 350 is a rate of 0.97 and means a
great deal. ``confidence_lower_bound`` is a Wilson score interval, so a small
sample is *scored* as uncertain rather than filtered by a separate minimum
that someone will eventually tune away.

**Trust can veto; it cannot grant.** ``autonomy_level`` is consumed alongside
policy, never instead of it — v6 §25 grants autonomy only when policy also
permits. A ``suspended`` profile blocks; an ``autonomous`` one merely stops
being the reason to block.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from contextedge.models.base import Base, TenantScopedMixin

# ADVISORY   — recommend only; the default for anything unproven.
# SUPERVISED — may execute with a human watching / approving.
# AUTONOMOUS — may execute unattended, IF policy also permits.
# SUSPENDED  — recent evidence says stop, regardless of the long-run record.
AUTONOMY_LEVELS = ("advisory", "supervised", "autonomous", "suspended")

# The placeholder used when a scope dimension is unknown. A literal rather than
# NULL because the scope is a unique key, and NULLs would let two "unknown
# environment" profiles coexist for the same agent and action.
UNSCOPED = "unspecified"


class TrustProfile(Base, TenantScopedMixin):
    __tablename__ = "trust_profiles"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "agent_ref",
            "action_type",
            "resource_class",
            "environment",
            "business_criticality",
            name="uq_trust_profiles_scope",
        ),
        CheckConstraint(
            "autonomy_level IN ('advisory', 'supervised', 'autonomous', 'suspended')",
            name="ck_trust_profiles_autonomy_level",
        ),
        CheckConstraint("sample_size >= 0", name="ck_trust_profiles_sample_size"),
        Index("ix_trust_profiles_lookup", "tenant_id", "agent_ref", "action_type"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )

    # --- scope ------------------------------------------------------------
    # Who acted. A user id today; a service or agent identity when one exists.
    agent_ref: Mapped[str] = mapped_column(String(120), nullable=False)
    action_type: Mapped[str] = mapped_column(String(60), nullable=False)
    # The CI class the action touched (B1 taxonomy key), not the instance —
    # a track record on one host does not transfer, but a record on
    # "windows_endpoint" is exactly the generalization worth having.
    resource_class: Mapped[str] = mapped_column(String(80), nullable=False)
    environment: Mapped[str] = mapped_column(String(30), nullable=False)
    business_criticality: Mapped[str] = mapped_column(String(30), nullable=False)

    # --- counters ---------------------------------------------------------
    sample_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    verified_successes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    inconclusive: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rollbacks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    human_overrides: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reopens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Outcomes since the last success — the streak that demotes a profile
    # without waiting for the long-run average to move.
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # --- derived ----------------------------------------------------------
    # Wilson score lower bound on the verified-success proportion. The number
    # the autonomy decision reads, so a small sample cannot pass on a lucky
    # streak.
    confidence_lower_bound: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0
    )
    autonomy_level: Mapped[str] = mapped_column(
        String(20), nullable=False, default="advisory"
    )
    # Why the level is what it is, in one line, for the operator who is about
    # to ask why their playbook will not run unattended.
    autonomy_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)

    last_outcome_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_evaluated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
