"""Celery dispatches wait for the transaction to become durable.

Dispatching inside the transaction fails in both directions: a rollback
leaves tasks naming rows that never existed (65 of them, live, on
2026-08-19), and on the success path a worker reading in the pre-commit
window gets "not found" and returns skipped, so the row silently never
gets its follow-up work.

Driven through a bare sync ``Session``: ``dispatch_after_commit`` only
touches ``db.sync_session``, and the commit/rollback events these tests
assert on are emitted by that session, so no database or async driver is
needed to exercise the real mechanism.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy.orm import Session

from contextedge.services.deferred_dispatch import dispatch_after_commit


class _Recorder:
    """Stands in for the Celery app and records what was sent."""

    def __init__(self):
        self.sent: list[tuple] = []

    def send_task(self, name, args=None, **kwargs):
        self.sent.append((name, args))


def _db():
    """An AsyncSession-shaped handle over a real (unbound) sync session."""
    sync_session = Session()
    return SimpleNamespace(sync_session=sync_session), sync_session


def test_nothing_is_sent_before_the_commit():
    """The point of the module: the task must not exist while the row is
    still invisible to every other connection."""
    db, sync_session = _db()
    recorder = _Recorder()

    with patch("contextedge.workers.celery_app.celery_app", recorder):
        dispatch_after_commit(db, "pattern.generate_playbook_candidate", ["p1", "t1"])
        assert recorder.sent == []  # queued, not sent

        sync_session.commit()

        assert recorder.sent == [("pattern.generate_playbook_candidate", ["p1", "t1"])]


def test_rollback_discards_the_dispatch():
    """A task naming a rolled-back row is pure noise in the queue — this is
    the case that put 65 of them there.

    The rollback is raised through the session's own event dispatch rather
    than by calling ``sync_session.rollback()``. SQLAlchemy emits
    ``after_rollback`` only for a real DBAPI rollback, and a session that
    never touched a database has no transaction to roll back — so calling
    it here would prove nothing about the production path, where the
    worker's ``run_async`` rolls back a transaction that did real work.
    """
    db, sync_session = _db()
    recorder = _Recorder()

    with patch("contextedge.workers.celery_app.celery_app", recorder):
        dispatch_after_commit(db, "pattern.generate_playbook_candidate", ["p1", "t1"])

        sync_session.dispatch.after_rollback(sync_session)
        sync_session.commit()  # a later, unrelated commit must not resurrect it

        assert recorder.sent == []


def test_the_rollback_listener_is_actually_registered():
    """Guards the test above: firing an event nobody listens to would pass
    for the wrong reason."""
    db, sync_session = _db()

    dispatch_after_commit(db, "task.one", ["a"])

    from contextedge.services import deferred_dispatch

    registered = [
        fn.__name__ for fn in sync_session.dispatch.after_rollback
    ]
    assert deferred_dispatch._drop_pending.__name__ in registered


def test_each_commit_drains_only_what_it_accumulated():
    """Sessions commit more than once; the second commit must not re-send
    the first one's tasks."""
    db, sync_session = _db()
    recorder = _Recorder()

    with patch("contextedge.workers.celery_app.celery_app", recorder):
        dispatch_after_commit(db, "task.one", ["a"])
        sync_session.commit()
        dispatch_after_commit(db, "task.two", ["b"])
        sync_session.commit()

        assert recorder.sent == [("task.one", ["a"]), ("task.two", ["b"])]


def test_a_broker_outage_does_not_undo_a_committed_transaction():
    """The row is durable by the time we send. Raising here would surface
    as a commit failure for work that actually succeeded."""
    db, sync_session = _db()

    def _explode(*_args, **_kwargs):
        raise RuntimeError("broker down")

    with patch(
        "contextedge.workers.celery_app.celery_app",
        SimpleNamespace(send_task=_explode),
    ):
        dispatch_after_commit(db, "pattern.generate_playbook_candidate", ["p1", "t1"])

        sync_session.commit()  # must not raise


def test_a_session_without_sync_session_does_not_break_the_caller():
    """The row is the product; the task is a consequence. Whatever the
    caller is holding, creating the row must still succeed."""
    recorder = _Recorder()

    with patch("contextedge.workers.celery_app.celery_app", recorder):
        dispatch_after_commit(
            SimpleNamespace(), "pattern.generate_playbook_candidate", ["p1", "t1"]
        )

    assert recorder.sent == []


def test_repeated_calls_register_the_hook_once():
    """Listeners are per-session; re-registering would send each task as
    many times as dispatch_after_commit had been called."""
    db, sync_session = _db()
    recorder = _Recorder()

    with patch("contextedge.workers.celery_app.celery_app", recorder):
        for _ in range(3):
            dispatch_after_commit(db, "task.one", ["a"])
        sync_session.commit()

        assert recorder.sent == [("task.one", ["a"])] * 3
