"""Recording what policy decided, at the moment it decided it (F3).

``approval_policy_service`` enforces real rules — an automation-mode cap, a
minimum safety class for approval, an approver-role requirement, a
self-approval ban — and none of it left a trace. ``DecisionActionPolicy``
stored a result with no policy version, so "which policy version evaluated
this, and what did it see?" had no answer for the engine that actually runs.

Two properties are deliberate:

- **The rule functions stay pure.** ``check_automation_mode`` and
  ``check_decider`` are synchronous, take no session, and raise. Recording
  lives here and is called by the executor, so the enforcement path cannot be
  slowed or broken by an audit write, and a test of the rule stays a test of
  the rule.
- **Recording never blocks the action.** A failed audit write is logged and
  swallowed. That is the right trade only because the write is additive
  evidence, not the gate itself: the gate has already raised by then.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.models.policy import POLICY_CHECK_RESULTS, PolicyCheck

logger = structlog.get_logger()


async def record_policy_check(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    policy_id: uuid.UUID | None,
    policy_version: int | None,
    policy_type: str,
    check_name: str,
    evaluated_entity_type: str,
    evaluated_entity_id: uuid.UUID | None,
    result: str,
    reason: str | None = None,
    input_snapshot: dict[str, Any] | None = None,
    evaluated_by: uuid.UUID | None = None,
) -> PolicyCheck | None:
    """Persist one policy evaluation. Returns None if it could not be written.

    ``policy_id`` / ``policy_version`` are None when no policy was configured —
    which is itself worth recording, as ``not_applicable``, so an auditor can
    tell "no rule applied" from "no check ran".
    """
    if result not in POLICY_CHECK_RESULTS:
        raise ValueError(f"result must be one of {POLICY_CHECK_RESULTS}, got {result!r}")
    try:
        row = PolicyCheck(
            tenant_id=tenant_id,
            policy_id=policy_id,
            policy_type=policy_type,
            policy_version=policy_version,
            check_name=check_name,
            evaluated_entity_type=evaluated_entity_type,
            evaluated_entity_id=evaluated_entity_id,
            result=result,
            reason=reason,
            input_snapshot=input_snapshot or {},
            evaluated_by=evaluated_by,
        )
        db.add(row)
        await db.flush()
        return row
    except Exception as exc:  # pragma: no cover - defensive, exercised by the test
        # Additive evidence, not the gate. The gate has already decided by the
        # time this runs, so a broken audit write must not turn an allowed
        # action into a failed one.
        logger.warning(
            "policy_check.record_failed",
            tenant_id=str(tenant_id),
            check_name=check_name,
            error=str(exc),
        )
        return None
