"""The tool a step actually runs, and the contract it runs under (F6).

``PlaybookStep.tool_ref`` has been a free string since it was introduced —
declared in the schema, set by nothing, resolved by nothing. There was no way
to ask what a step would invoke, what happens if it times out, whether running
it twice is safe, or whether it can be dry-run. The execution ledger recorded
tool invocations that no definition governed.

Two objects, deliberately separate:

- ``Skill`` — WHAT can be invoked: interface, I/O schemas, reversibility, the
  rollback skill, who may run it, its side-effect class.
- ``ExecutionContract`` — HOW it must be invoked: idempotency, timeout,
  retries, cancellation, dry-run, concurrency, rate limit, credential scope.

They are separate because the same operational contract governs many skills (a
whole family of ServiceNow API calls shares one timeout/retry/rate-limit
posture), and because the contract is what F8's attempt model reads while the
skill is what the planner reads. Folding the contract into the skill would
have made every skill restate the same operational envelope, and restated
constants drift.

**Side-effect classification reuses ``SAFETY_CLASSES``** rather than minting
v6's parallel vocabulary. The executor already gates on that tuple, the
approval policy already thresholds on it, and a second vocabulary meaning the
same thing is the drift this epic keeps closing.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from contextedge.models.base import Base, TenantScopedMixin

# How the skill is reached. MANUAL is first-class: a step a human performs is
# still a step the plan depends on, and modelling it as "no skill" loses the
# I/O contract and the verification hook.
INTERFACE_TYPES = ("API", "MCP", "RPA", "CLI", "SCRIPT", "WORKFLOW", "MANUAL")

# Whether running the same call twice is safe, and how safety is achieved.
#   NATIVE         — the tool itself is idempotent; replay is harmless.
#   CALLER_KEY     — safe only when the caller supplies an idempotency key.
#   DEDUPE_ONLY    — not idempotent, but duplicate calls inside
#                    ``deduplication_window_sec`` are suppressed.
#   NOT_IDEMPOTENT — replay does the thing again. Legal only for read-only and
#                    low-side-effect skills; see ``models/skill`` validation.
IDEMPOTENCY_MODES = ("NATIVE", "CALLER_KEY", "DEDUPE_ONLY", "NOT_IDEMPOTENT")

# Idempotency modes that provide a replay guarantee of some kind.
REPLAY_SAFE_MODES = ("NATIVE", "CALLER_KEY", "DEDUPE_ONLY")

CONCURRENCY_POLICIES = ("parallel", "serial_per_target", "serial_global")

RETRY_BACKOFFS = ("none", "fixed", "exponential")

SKILL_STATUSES = ("draft", "active", "deprecated", "retired")


class ExecutionContract(Base, TenantScopedMixin):
    """The operational envelope a skill must be invoked inside."""

    __tablename__ = "execution_contracts"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_execution_contracts_tenant_name"),
        CheckConstraint(
            "idempotency_mode IN ('NATIVE', 'CALLER_KEY', 'DEDUPE_ONLY', 'NOT_IDEMPOTENT')",
            name="ck_execution_contracts_idempotency_mode",
        ),
        CheckConstraint("timeout_sec > 0", name="ck_execution_contracts_timeout"),
        CheckConstraint("max_attempts >= 1", name="ck_execution_contracts_max_attempts"),
        CheckConstraint(
            "deduplication_window_sec IS NULL OR deduplication_window_sec > 0",
            name="ck_execution_contracts_dedup_window",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    idempotency_mode: Mapped[str] = mapped_column(String(20), nullable=False)
    # Only meaningful for DEDUPE_ONLY; NULL otherwise, rather than a zero that
    # would read as "dedupe for no time at all".
    deduplication_window_sec: Mapped[int | None] = mapped_column(Integer, nullable=True)

    timeout_sec: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    retry_backoff: Mapped[str] = mapped_column(String(20), nullable=False, default="none")

    supports_cancellation: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    supports_dry_run: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    concurrency_policy: Mapped[str] = mapped_column(
        String(30), nullable=False, default="parallel"
    )
    max_concurrency: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rate_limit_per_minute: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Which credential the skill runs as. A name, never a secret — the secret
    # lives wherever credentials live, and copying it here would put it in
    # every audit read of the contract.
    credential_scope: Mapped[str | None] = mapped_column(String(120), nullable=True)
    expected_duration_sec: Mapped[int | None] = mapped_column(Integer, nullable=True)

    contract_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    @property
    def is_replay_safe(self) -> bool:
        """Whether a retried call is safe under this contract."""
        return self.idempotency_mode in REPLAY_SAFE_MODES


class Skill(Base, TenantScopedMixin):
    """A registered, versioned thing a playbook step can invoke."""

    __tablename__ = "skills"
    __table_args__ = (
        UniqueConstraint("tenant_id", "skill_key", "version", name="uq_skills_key_version"),
        CheckConstraint(
            "interface_type IN ('API', 'MCP', 'RPA', 'CLI', 'SCRIPT', 'WORKFLOW', 'MANUAL')",
            name="ck_skills_interface_type",
        ),
        CheckConstraint(
            "safety_class IN ('read_only', 'low_side_effect', 'high_side_effect', 'destructive')",
            name="ck_skills_safety_class",
        ),
        CheckConstraint(
            "status IN ('draft', 'active', 'deprecated', 'retired')",
            name="ck_skills_status",
        ),
        Index("ix_skills_tenant_key_status", "tenant_id", "skill_key", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    # Stable across versions — what a step's ``tool_ref`` names.
    skill_key: Mapped[str] = mapped_column(String(120), nullable=False)
    version: Mapped[str] = mapped_column(String(20), nullable=False, default="1.0.0")
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # The controlled identifier the policy engine matches on (F1's ACTION_TYPES).
    action_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    interface_type: Mapped[str] = mapped_column(String(20), nullable=False)
    # Endpoint, MCP tool name, RPA process id — how the interface is addressed.
    endpoint_or_tool: Mapped[str | None] = mapped_column(String(500), nullable=True)

    input_schema: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    output_schema: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Reversibility is modelled independently of success (v6 §Reversibility):
    # a skill can succeed and still be irreversible.
    reversible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    rollback_skill_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("skills.id", ondelete="SET NULL"), nullable=True
    )

    # Doubles as v6's sideEffectClassification — same tuple the executor and
    # the approval policy already gate on.
    safety_class: Mapped[str] = mapped_column(String(30), nullable=False)
    allowed_principal_roles: Mapped[list] = mapped_column(
        JSONB, server_default="[]", nullable=False
    )

    execution_contract_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("execution_contracts.id", ondelete="RESTRICT"),
        nullable=True,
    )

    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
