"""The action-policy engine (F3b).

``action_policies`` shipped in ``0029`` with a verdict vocabulary
(``allowed_auto`` … ``manual_only``), scope axes, and precedence columns whose
docstring said the engine was "on the design roadmap". Nothing wrote the table,
nothing queried it outside the agent projection, and ``Decision.policy_result``
— documented as "the verdict the executor checks" — had no verdict to hold.

F3b was deliberately deferred while a policy engine would have had nothing to
gate. **F1 changed that**: ``ExecutionStepRun.action_name`` is now written, so
the lookup key this table is designed around exists on every step.

Three rules decide which policy applies, in order:

1. **Scope filter.** A policy applies when every scope axis it *declares*
   matches the request. A NULL axis is "any" — a policy that names no
   environment governs all of them.
2. **Specificity.** Among applicable policies, the one that declared more
   matching axes wins. A rule about ``restart_service`` on THIS workflow in
   production is more about the situation than one about ``restart_service``
   everywhere, and precedence that ignored that would make narrow rules
   pointless to write.
3. **Conflict resolution.** Only for a genuine tie on specificity. The default
   is ``most_restrictive``, and it is the default deliberately: when two rules
   at the same specificity disagree about whether something may run
   unattended, the safe reading is the one that asks a human.
   ``highest_priority`` is available for tenants that need an explicit
   override, and ties there fall back to most-restrictive rather than to row
   order, because row order is not a decision anyone made.

The verdict **never loosens** what the rest of the executor decided. It can
force approval or refuse; it cannot grant an autonomy that safety class, role
or trust withheld — the same rule F10 follows.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import structlog
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.models.action_policy import POLICY_RESULTS, ActionPolicy

logger = structlog.get_logger()

# Restrictiveness order, least to most. ``most_restrictive`` conflict
# resolution reads this, and so does the executor when deciding what a verdict
# forces. ``restricted`` is last: it is the only verdict that says "not by
# anyone, not by hand" rather than "not like this".
RESTRICTIVENESS: tuple[str, ...] = (
    "allowed_auto",
    "approval_required",
    "recommendation_only",
    "manual_only",
    "restricted",
)

# Verdicts under which an execution must not proceed at all.
BLOCKING_RESULTS = ("recommendation_only", "manual_only", "restricted")

CONFLICT_RESOLUTIONS = ("most_restrictive", "highest_priority")

# Scope axes, in the order they are reported. Each is optional on the policy;
# a NULL means "any value of this axis".
SCOPE_AXES = ("workflow_entity_id", "environment", "business_unit", "data_domain")


class ActionPolicyError(ValueError):
    """A policy that cannot be stored as described."""


def restrictiveness(result: str) -> int:
    """Rank a verdict. An unknown verdict ranks MOST restrictive.

    Fail closed, for the same reason ``_safety_class_rank`` does: a typo in a
    policy must never read as ``allowed_auto``.
    """
    try:
        return RESTRICTIVENESS.index(result)
    except ValueError:
        return len(RESTRICTIVENESS)


def _matches(policy: ActionPolicy, request: dict) -> bool:
    """Does this policy's declared scope fit the request?"""
    for axis in SCOPE_AXES:
        declared = getattr(policy, axis, None)
        if declared is None:
            continue  # "any"
        if request.get(axis) != declared:
            return False
    return True


def specificity(policy: ActionPolicy) -> int:
    """How many scope axes this policy pins down."""
    return sum(1 for axis in SCOPE_AXES if getattr(policy, axis, None) is not None)


def in_effect(policy: ActionPolicy, at: datetime) -> bool:
    start = policy.effective_from
    end = policy.effective_to
    if start is not None:
        start = start if start.tzinfo else start.replace(tzinfo=UTC)
        if at < start:
            return False
    if end is not None:
        end = end if end.tzinfo else end.replace(tzinfo=UTC)
        if at >= end:
            return False
    return True


def select_policy(
    policies: list[ActionPolicy], request: dict, at: datetime
) -> ActionPolicy | None:
    """The winning policy for a request, or None if none applies.

    Pure, so the precedence rules can be tested without a database — which
    matters because precedence is the part everyone gets wrong.
    """
    applicable = [
        p
        for p in policies
        if p.is_active and in_effect(p, at) and _matches(p, request)
    ]
    if not applicable:
        return None

    best = max(specificity(p) for p in applicable)
    finalists = [p for p in applicable if specificity(p) == best]
    if len(finalists) == 1:
        return finalists[0]

    # A genuine tie. Strategy comes from the finalists themselves; if they
    # disagree about the strategy, the most restrictive reading wins that
    # argument too.
    strategies = {p.conflict_resolution for p in finalists}
    if strategies == {"highest_priority"}:
        top = max(p.priority for p in finalists)
        contenders = [p for p in finalists if p.priority == top]
        if len(contenders) == 1:
            return contenders[0]
        finalists = contenders

    # Most restrictive. Ties beyond that are resolved by policy name so the
    # answer is stable across runs — row order is not a decision anyone made.
    return sorted(
        finalists, key=lambda p: (-restrictiveness(p.policy_result), p.policy_name)
    )[0]


async def evaluate_action(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    action_name: str,
    workflow_entity_id: uuid.UUID | None = None,
    environment: str | None = None,
    business_unit: str | None = None,
    data_domain: str | None = None,
    at: datetime | None = None,
) -> ActionPolicy | None:
    """Load the candidate policies for an action and pick the winner."""
    at = at or datetime.now(UTC)
    candidates = (
        (
            await db.execute(
                select(ActionPolicy).where(
                    ActionPolicy.tenant_id == tenant_id,
                    ActionPolicy.action_name == action_name,
                    ActionPolicy.is_active.is_(True),
                    or_(
                        ActionPolicy.workflow_entity_id.is_(None),
                        ActionPolicy.workflow_entity_id == workflow_entity_id,
                    ),
                )
            )
        )
        .scalars()
        .all()
    )
    request = {
        "workflow_entity_id": workflow_entity_id,
        "environment": environment,
        "business_unit": business_unit,
        "data_domain": data_domain,
    }
    winner = select_policy(list(candidates), request, at)
    if winner is not None:
        logger.info(
            "action_policy.evaluated",
            tenant_id=str(tenant_id),
            action_name=action_name,
            policy_id=str(winner.id),
            policy_version=winner.version,
            policy_result=winner.policy_result,
        )
    return winner


def validate_policy_fields(
    *, policy_result: str, conflict_resolution: str, risk_level: str
) -> None:
    if policy_result not in POLICY_RESULTS:
        raise ActionPolicyError(f"policy_result must be one of {POLICY_RESULTS}")
    if conflict_resolution not in CONFLICT_RESOLUTIONS:
        raise ActionPolicyError(
            f"conflict_resolution must be one of {CONFLICT_RESOLUTIONS}"
        )
    from contextedge.models.action_policy import RISK_LEVELS

    if risk_level not in RISK_LEVELS:
        raise ActionPolicyError(f"risk_level must be one of {RISK_LEVELS}")
