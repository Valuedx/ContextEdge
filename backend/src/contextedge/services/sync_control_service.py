"""Pause, resume and cancel a sync that is already running.

A backfill spends its time inside ONE `connector.backfill()` call — measured
at 913 seconds on the live Zoho corpus for a page walk plus 1,855 sequential
detail fetches — and writes nothing until it returns. So a control signal
consulted between calls would do nothing for a quarter of an hour, and a
pause button that behaves that way is worse than no button: the operator
believes they stopped something that is still running.

The signal is therefore read *inside* the connector's loops, once per page
and once per 25 records of the detail fetch, through the callback this module
builds. A stop lands in seconds.

**A stop keeps what was already fetched.** Nine hundred seconds of API calls
must not be discarded because somebody clicked pause: the connector returns
the records it has collected together with its checkpoint, the job persists
them, and a resume continues from there instead of restarting.

The difference between the two stops is only what happens next:

- `pause` — the run ends as `paused` and the object stays approved, so
  `resume` (or the next scheduled tick) carries on from the checkpoint.
- `cancel` — the run ends as `cancelled` and nothing restarts it. What was
  already ingested stays ingested; cancelling a sync is not a rollback, and
  pretending otherwise would delete real evidence.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.models.source import SyncRun

logger = structlog.get_logger()

CONTROL_SIGNALS = ("pause", "cancel")
# Terminal states a controlled stop produces.
CONTROL_STATUSES = {"pause": "paused", "cancel": "cancelled"}


async def active_run(
    db: AsyncSession, *, tenant_id: uuid.UUID, source_object_id: uuid.UUID
) -> SyncRun | None:
    """The run currently executing for this object, if any."""
    return (
        await db.execute(
            select(SyncRun)
            .where(
                SyncRun.tenant_id == tenant_id,
                SyncRun.source_object_id == source_object_id,
                SyncRun.status == "running",
            )
            .order_by(SyncRun.started_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def signal_run(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    run_id: uuid.UUID,
    action: str,
) -> SyncRun | None:
    """Ask a running sync to stop. Returns the run, or None if not found.

    Setting the signal is all this does — the running job is what acts on it.
    Deliberately: a control that reached in and killed the worker would lose
    the fetched-but-unpersisted records the cooperative stop preserves.
    """
    if action not in CONTROL_SIGNALS:
        raise ValueError(f"action must be one of {list(CONTROL_SIGNALS)}")
    run = await db.get(SyncRun, run_id)
    if run is None or run.tenant_id != tenant_id:
        return None
    if run.status != "running":
        # Nothing to stop. Not an error: the run may have finished between
        # the operator reading the screen and pressing the button.
        return run
    run.control = action
    await db.flush()
    logger.info(
        "sync_control.signalled",
        run_id=str(run.id),
        action=action,
        source_object_id=str(run.source_object_id),
    )
    return run


def control_check_for(session_factory, run_id: uuid.UUID):
    """Build the callback the connector consults.

    Its own session, deliberately: the sync job holds a transaction open
    across the whole fetch, and a signal written by the API afterwards would
    be invisible inside it. A separate short read sees the current row.

    Never raises — a control channel that fails must not take down the sync
    it exists to manage. Returning None means "carry on", which is the safe
    reading of "I could not tell".
    """

    async def _check() -> str | None:
        try:
            async with session_factory() as probe:
                from contextedge.tenant_rls import bind_session_tenant

                await bind_session_tenant(probe, None, bypass=True)
                signal = (
                    await probe.execute(
                        select(SyncRun.control).where(SyncRun.id == run_id)
                    )
                ).scalar_one_or_none()
                return signal if signal in CONTROL_SIGNALS else None
        except Exception as exc:  # noqa: BLE001
            logger.warning("sync_control.check_failed", error_type=type(exc).__name__)
            return None

    return _check


def finalize_status(control: str | None, default: str = "completed") -> str:
    """What a run that stopped under a signal should be recorded as."""
    return CONTROL_STATUSES.get(control or "", default)


async def clear_control(db: AsyncSession, run: SyncRun) -> None:
    """Drop the signal once it has been acted on, so a resumed run does not
    inherit the stop that ended its predecessor."""
    run.control = None
    run.completed_at = run.completed_at or datetime.now(UTC)
    await db.flush()
