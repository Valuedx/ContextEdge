"""Making the idempotency key real (F8).

``ExecutionStepRun.idempotency_key`` and its partial unique index shipped in
``0029`` and were described as "the single most important banking-grade safety
control in the alignment". Nothing ever wrote the column, so the index guarded
a value that was always NULL and the control was inert.

**What makes two executions the same action.** The key is derived from the
artifact hash F7 already computes, scoped to the case. Same case, same step
payload, same action — so a re-run is a *retry of the same logical operation*,
which is exactly what an idempotency key is for, and gets another attempt
rather than another side effect. A different case is a different incident and
legitimately does the thing again.

**Which steps get a key.** Only side-effecting ones. Re-running a diagnostic
is normal and useful, and a key that suppressed the second `get_service_status`
would be a bug wearing a safety control's clothes. This mirrors the registry's
own rule that a read-only skill needs no replay guarantee.

**Skills with native idempotency get no key either.** If the tool is already
safe to replay, a caller key adds a suppression the tool did not ask for and
the operator did not expect.
"""

from __future__ import annotations

import hashlib
import uuid

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.models.execution import SAFETY_CLASSES, ExecutionStepRun

logger = structlog.get_logger()

# Safety classes whose replay is worth suppressing. Read-only is excluded on
# purpose — see the module docstring.
KEYED_SAFETY_CLASSES = tuple(c for c in SAFETY_CLASSES if c != "read_only")

DUPLICATE_CHECK_PASSED = "passed"
DUPLICATE_CHECK_DUPLICATE = "duplicate"
DUPLICATE_CHECK_NOT_APPLICABLE = "not_applicable"


def needs_idempotency_key(safety_class: str, idempotency_mode: str | None) -> bool:
    """Whether this step's replay should be suppressed.

    ``idempotency_mode`` is the bound skill's contract mode, or None when the
    step is unbound. Unbound side-effecting steps DO get a key: without a
    contract we cannot know the tool is safe to replay, and the conservative
    answer is the one that suppresses.
    """
    if safety_class not in KEYED_SAFETY_CLASSES:
        return False
    # NATIVE means the tool is already safe to replay; adding a caller key
    # would impose a suppression the tool did not ask for.
    return idempotency_mode != "NATIVE"


def derive_idempotency_key(
    *,
    tenant_id: uuid.UUID,
    scope_id: uuid.UUID | None,
    artifact_hash: str,
) -> str:
    """A stable key for "this action, in this case".

    ``scope_id`` is the case (resolution session). A run with no case is scoped
    to itself, so it cannot collide with anything — an ad-hoc execution outside
    a case has no prior occurrence to be a duplicate of.

    Hashed rather than concatenated because the index is global and the parts
    include tenant ids: a readable key would leak tenant identity into a column
    other tenants' rows share an index with.
    """
    material = f"{tenant_id}:{scope_id or 'no-case'}:{artifact_hash}"
    return "idem_" + hashlib.sha256(material.encode("utf-8")).hexdigest()


async def find_duplicate(
    db: AsyncSession, tenant_id: uuid.UUID, idempotency_key: str
) -> ExecutionStepRun | None:
    """The earlier step-run this key already belongs to, if any."""
    return (
        await db.execute(
            select(ExecutionStepRun)
            .where(
                ExecutionStepRun.tenant_id == tenant_id,
                ExecutionStepRun.idempotency_key == idempotency_key,
            )
            .order_by(ExecutionStepRun.created_at)
            .limit(1)
        )
    ).scalar_one_or_none()
