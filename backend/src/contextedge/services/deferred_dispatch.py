"""Celery dispatches that wait for the transaction to become durable.

Services that create a row and then queue work about that row have to
send the task *after* the commit, never inside it. Dispatching early is
wrong in both directions:

- **On rollback**, the row disappears but the task does not. A clustering
  pass that rolled back after creating patterns left 65 queued
  ``generate_playbook_candidate`` tasks naming patterns that never
  existed (observed 2026-08-19). Those fail harmlessly, but they make a
  queue that is mostly junk look like real work.
- **On success**, the worker can pick the task up in the window before
  the commit lands, read "not found", and return ``skipped``. The row is
  real, the task is gone, and nothing retries — a pattern silently never
  gets its playbook. That is the failure this module exists to prevent.

The episode review path already follows the rule by hand, committing per
episode before dispatching. This makes it available to services that do
not own their commit — anything called inside ``run_async`` or a FastAPI
``get_db`` dependency, where the caller decides when the transaction ends.

Usage::

    dispatch_after_commit(db, "pattern.generate_playbook_candidate",
                          [str(pattern.id), str(tenant_id)])

Routing is unchanged: ``send_task`` applies the same ``task_routes`` that
``.delay()`` would, so queues stay as configured.
"""

from __future__ import annotations

from typing import Any

import structlog
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger()

_PENDING_KEY = "contextedge_pending_dispatches"
_HOOKED_KEY = "contextedge_dispatch_hooked"


def _send_pending(session: Any) -> None:
    """Fire everything queued during the transaction that just committed."""
    pending = session.info.pop(_PENDING_KEY, None)
    if not pending:
        return
    from contextedge.workers.celery_app import celery_app

    for task_name, args in pending:
        try:
            celery_app.send_task(task_name, args=args)
        except Exception as exc:  # noqa: BLE001 — a broker outage must not
            # undo a committed transaction. The row is already durable; the
            # dispatch is best-effort and its absence is recoverable by the
            # orphan sweeps that already exist.
            logger.warning(
                "deferred_dispatch.send_failed",
                task=task_name,
                args=args,
                error=str(exc),
            )


def _drop_pending(session: Any) -> None:
    """Discard queued dispatches when the transaction is thrown away."""
    session.info.pop(_PENDING_KEY, None)


def dispatch_after_commit(db: AsyncSession, task_name: str, args: list[Any]) -> None:
    """Queue a Celery task to be sent once ``db``'s transaction commits.

    Safe to call repeatedly; the listeners are registered once per session
    and each commit drains whatever accumulated since the last one.
    """
    sync_session = getattr(db, "sync_session", None)
    if sync_session is None:
        # Queuing follow-up work must never break the write that produced
        # it — the row is the product, the task is a consequence. The
        # dispatch this replaced was wrapped in try/except for the same
        # reason. Reached by session doubles in tests, and by any caller
        # holding something that is not a real AsyncSession.
        logger.warning(
            "deferred_dispatch.no_sync_session",
            task=task_name,
            session_type=type(db).__name__,
        )
        return
    sync_session.info.setdefault(_PENDING_KEY, []).append((task_name, list(args)))
    if not sync_session.info.get(_HOOKED_KEY):
        sync_session.info[_HOOKED_KEY] = True
        event.listen(sync_session, "after_commit", _send_pending)
        event.listen(sync_session, "after_rollback", _drop_pending)
