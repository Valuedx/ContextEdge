"""Registering skills, and resolving what a step will actually invoke (F6).

Three things live here, and the order matters:

1. **Registration invariants.** A skill that changes the world must declare how
   it behaves when the call is retried, and what its timeout is. Enforcing that
   at registration is the cheapest possible place — before a planner can select
   it, before an approver can approve it, before an executor exists to run it.
2. **Resolution.** ``PlaybookStep.tool_ref`` becomes a reference:
   ``skill_key`` (the active version) or ``skill_key@version`` (pinned).
3. **The publish gate.** A step that names a tool must name one that exists.

The invariants, and why each is where it is:

- **A side-effecting skill needs a contract.** Without one there is no timeout,
  no retry policy, and no statement about replay — the executor would be
  inventing all three at call time, differently in each code path.
- **A high-side-effect or destructive skill may not be ``NOT_IDEMPOTENT``.**
  This is v6 invariant 8 ("no at-least-once side-effect execution without
  idempotency/deduplication controls") enforced at the earliest point it can
  be. A genuinely non-idempotent destructive tool is not blocked from the
  system — it is blocked from being registered *as if* replay were safe. The
  answer is ``CALLER_KEY`` with the key F8 will generate, or ``DEDUPE_ONLY``
  with a window.
- **``DEDUPE_ONLY`` needs a window.** Deduplicating over an unspecified period
  is not a guarantee, it is a hope.
"""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.models.execution import ACTION_TYPES, SAFETY_CLASSES
from contextedge.models.skill import (
    CONCURRENCY_POLICIES,
    IDEMPOTENCY_MODES,
    INTERFACE_TYPES,
    RETRY_BACKOFFS,
    SKILL_STATUSES,
    ExecutionContract,
    Skill,
)

logger = structlog.get_logger()

# Safety classes for which the registry demands a replay guarantee.
REPLAY_GUARANTEE_REQUIRED = ("high_side_effect", "destructive")
# Safety classes that must carry an execution contract at all.
CONTRACT_REQUIRED = ("low_side_effect", "high_side_effect", "destructive")


class SkillRegistryError(ValueError):
    """A skill or contract that may not be registered as described."""


class UnresolvedSkillReference(SkillRegistryError):
    """A step names a tool the registry does not know."""


def validate_contract(contract: ExecutionContract) -> None:
    """Contract-internal consistency, independent of any skill."""
    if contract.idempotency_mode == "DEDUPE_ONLY" and not contract.deduplication_window_sec:
        raise SkillRegistryError(
            "DEDUPE_ONLY requires deduplication_window_sec — deduplicating over an "
            "unspecified period is not a guarantee"
        )
    if contract.max_attempts > 1 and not contract.is_replay_safe:
        raise SkillRegistryError(
            f"max_attempts={contract.max_attempts} with idempotency_mode="
            f"{contract.idempotency_mode!r}: retrying a call with no replay "
            "guarantee is how an action happens twice"
        )


def validate_skill(skill: Skill, contract: ExecutionContract | None) -> None:
    """The registration invariants. Raises ``SkillRegistryError`` on any breach."""
    if skill.interface_type not in INTERFACE_TYPES:
        raise SkillRegistryError(f"interface_type must be one of {INTERFACE_TYPES}")
    if skill.safety_class not in SAFETY_CLASSES:
        raise SkillRegistryError(f"safety_class must be one of {SAFETY_CLASSES}")
    if skill.status not in SKILL_STATUSES:
        raise SkillRegistryError(f"status must be one of {SKILL_STATUSES}")
    if skill.action_type is not None and skill.action_type not in ACTION_TYPES:
        raise SkillRegistryError(f"action_type must be one of {ACTION_TYPES}")

    if skill.safety_class in CONTRACT_REQUIRED and contract is None:
        raise SkillRegistryError(
            f"a {skill.safety_class} skill needs an execution contract: without one it "
            "has no timeout, no retry policy and no statement about replay, and the "
            "executor would invent all three at call time"
        )
    if contract is None:
        return

    validate_contract(contract)

    if (
        skill.safety_class in REPLAY_GUARANTEE_REQUIRED
        and contract.idempotency_mode == "NOT_IDEMPOTENT"
    ):
        raise SkillRegistryError(
            f"a {skill.safety_class} skill may not register as NOT_IDEMPOTENT. Use "
            "CALLER_KEY (the executor supplies the key) or DEDUPE_ONLY with a window "
            "— at-least-once delivery plus an unguarded side effect is how a "
            "remediation runs twice"
        )
    # A skill that claims reversibility should say what reverses it. Not fatal:
    # some reversals are manual and have no skill of their own, and refusing
    # the registration would push those out of the registry entirely, which is
    # worse than an unbound rollback.
    if skill.reversible and skill.rollback_skill_id is None:
        logger.info(
            "skill_registry.reversible_without_rollback_skill",
            skill_key=skill.skill_key,
            note="reversal is presumed manual",
        )


async def register_execution_contract(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    name: str,
    idempotency_mode: str,
    timeout_sec: int = 60,
    description: str | None = None,
    deduplication_window_sec: int | None = None,
    max_attempts: int = 1,
    retry_backoff: str = "none",
    supports_cancellation: bool = False,
    supports_dry_run: bool = False,
    concurrency_policy: str = "parallel",
    max_concurrency: int | None = None,
    rate_limit_per_minute: int | None = None,
    credential_scope: str | None = None,
    expected_duration_sec: int | None = None,
    contract_version: int = 1,
) -> ExecutionContract:
    """Create a contract, or raise if it is not internally consistent.

    Every field is a parameter rather than a ``**kwargs`` blob on purpose: a
    contract that silently accepts an unknown key is a contract whose
    guarantees are whatever the caller happened to spell correctly.
    """
    if idempotency_mode not in IDEMPOTENCY_MODES:
        raise SkillRegistryError(f"idempotency_mode must be one of {IDEMPOTENCY_MODES}")
    if concurrency_policy not in CONCURRENCY_POLICIES:
        raise SkillRegistryError(f"concurrency_policy must be one of {CONCURRENCY_POLICIES}")
    if retry_backoff not in RETRY_BACKOFFS:
        raise SkillRegistryError(f"retry_backoff must be one of {RETRY_BACKOFFS}")
    if timeout_sec <= 0:
        raise SkillRegistryError(
            "timeout_sec must be positive — an unbounded call is not a contract"
        )

    contract = ExecutionContract(
        tenant_id=tenant_id,
        name=name.strip(),
        description=description,
        idempotency_mode=idempotency_mode,
        deduplication_window_sec=deduplication_window_sec,
        timeout_sec=timeout_sec,
        max_attempts=max_attempts,
        retry_backoff=retry_backoff,
        supports_cancellation=supports_cancellation,
        supports_dry_run=supports_dry_run,
        concurrency_policy=concurrency_policy,
        max_concurrency=max_concurrency,
        rate_limit_per_minute=rate_limit_per_minute,
        credential_scope=credential_scope,
        expected_duration_sec=expected_duration_sec,
        contract_version=contract_version,
    )
    validate_contract(contract)
    db.add(contract)
    await db.flush()
    return contract


async def register_skill(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    skill_key: str,
    name: str,
    interface_type: str,
    safety_class: str,
    version: str = "1.0.0",
    description: str | None = None,
    action_type: str | None = None,
    endpoint_or_tool: str | None = None,
    input_schema: dict | None = None,
    output_schema: dict | None = None,
    reversible: bool = False,
    rollback_skill_id: uuid.UUID | None = None,
    allowed_principal_roles: list[str] | None = None,
    execution_contract_id: uuid.UUID | None = None,
    status: str = "draft",
    created_by: uuid.UUID | None = None,
) -> Skill:
    """Register a skill, or raise if it may not be registered as described."""
    contract: ExecutionContract | None = None
    if execution_contract_id is not None:
        contract = await db.get(ExecutionContract, execution_contract_id)
        if contract is None or contract.tenant_id != tenant_id:
            raise SkillRegistryError(
                f"execution contract {execution_contract_id} not found for this tenant"
            )

    skill = Skill(
        tenant_id=tenant_id,
        skill_key=skill_key.strip(),
        version=version.strip(),
        name=name.strip(),
        description=description,
        action_type=action_type,
        interface_type=interface_type,
        endpoint_or_tool=endpoint_or_tool,
        input_schema=input_schema,
        output_schema=output_schema,
        reversible=reversible,
        rollback_skill_id=rollback_skill_id,
        safety_class=safety_class,
        allowed_principal_roles=allowed_principal_roles or [],
        execution_contract_id=execution_contract_id,
        status=status,
        created_by=created_by,
    )
    validate_skill(skill, contract)
    db.add(skill)
    await db.flush()
    return skill


def parse_tool_ref(tool_ref: str) -> tuple[str, str | None]:
    """``"restart_service@2.0.0"`` -> ``("restart_service", "2.0.0")``.

    An unpinned reference resolves to the active version, which is the right
    default for authoring and the wrong one for an approved artifact — F7's
    hash binding is what freezes the resolution at approval time.
    """
    key, _, version = tool_ref.strip().partition("@")
    return key.strip(), (version.strip() or None)


async def resolve_skill(
    db: AsyncSession, tenant_id: uuid.UUID, tool_ref: str
) -> Skill:
    """Resolve a step's ``tool_ref``, or raise ``UnresolvedSkillReference``."""
    key, version = parse_tool_ref(tool_ref)
    if not key:
        raise UnresolvedSkillReference("tool_ref is empty")

    query = select(Skill).where(Skill.tenant_id == tenant_id, Skill.skill_key == key)
    if version is not None:
        query = query.where(Skill.version == version)
    else:
        # Unpinned resolves to an ACTIVE version only. A draft skill is not
        # something a playbook should silently bind to, and a retired one is
        # exactly what a stale reference would otherwise keep pointing at.
        query = query.where(Skill.status == "active")
    query = query.order_by(Skill.created_at.desc()).limit(1)

    skill = (await db.execute(query)).scalar_one_or_none()
    if skill is None:
        raise UnresolvedSkillReference(
            f"no skill matches tool_ref {tool_ref!r}"
            + ("" if version else " (no ACTIVE version registered)")
        )
    return skill


async def validate_step_bindings(
    db: AsyncSession, tenant_id: uuid.UUID, steps: list | None
) -> dict[int, Skill]:
    """Resolve every step that names a tool. Raises on the first that fails.

    Steps that name no tool are left alone: they are manual or not yet bound,
    and that is the honest state of almost every playbook today. Requiring a
    binding for them would block the reviewer console rather than improve
    anything — the stronger rule ("a step the executor will run must be bound")
    belongs with the executor.
    """
    resolved: dict[int, Skill] = {}
    for index, step in enumerate(steps or []):
        if not isinstance(step, dict):
            continue
        tool_ref = step.get("tool_ref")
        if not isinstance(tool_ref, str) or not tool_ref.strip():
            continue
        try:
            resolved[index] = await resolve_skill(db, tenant_id, tool_ref)
        except UnresolvedSkillReference as exc:
            raise UnresolvedSkillReference(f"step {index}: {exc}") from exc
    return resolved
