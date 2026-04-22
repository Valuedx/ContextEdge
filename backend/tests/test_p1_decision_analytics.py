"""Tests for decision analytics fan-out (W10-12.1).

``evaluation.calibrate_decision_confidence`` and
``evaluation.mine_decision_patterns`` both accept the literal string
``"all"`` to loop every tenant in the DB. This matches the pattern
used by ``detect_drift`` / ``scan_contradictions_task`` so Celery Beat
can schedule one row per analytic, not one per tenant.

These tests exercise the fan-out logic by driving the work function
through a fake ``run_async`` — cheaper than spinning up a real Celery
+ PostgreSQL harness and enough to catch the fan-out regressions we
actually care about (correct tenant iteration, per-tenant exception
isolation, "all" vs single-tenant result shape).
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from contextedge.workers import decision_tasks


class _ScalarsAll:
    """Mimics a SQLAlchemy result for ``.all()`` + ``.scalar()``.

    Rows passed in are tuples; ``.scalar()`` returns the first element
    of the first row (matching real SQLAlchemy behaviour), ``.all()``
    returns the row list as-is."""

    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows

    def scalar(self):
        if not self._rows:
            return None
        first = self._rows[0]
        if isinstance(first, tuple):
            return first[0]
        return first


def _make_db(execute_side_effect, add_list=None):
    add_list = add_list if add_list is not None else []
    db = SimpleNamespace(
        execute=AsyncMock(side_effect=execute_side_effect),
        add=lambda obj: add_list.append(obj),
        flush=AsyncMock(),
    )
    return db


def _run_task_with_db(task_fn, db, *args):
    """Drive a Celery-decorated task synchronously against a mock DB.

    We patch ``run_async`` so the inner ``async def work(db)`` closure
    runs directly — Celery's retry/bind/broker machinery is out of
    scope for this test. Calling ``.run(*args)`` on a bound Celery
    task is the documented way to invoke the underlying function with
    ``self`` wired automatically.
    """
    def fake_run_async(fn):
        return asyncio.run(fn(db))

    with patch.object(decision_tasks, "run_async", fake_run_async):
        return task_fn.run(*args)


# ---------------------------------------------------------------------------
# mine_decision_patterns
# ---------------------------------------------------------------------------


def test_mine_single_tenant_returns_per_tenant_shape():
    tenant_id = uuid4()

    # Row tuples: (decision_type, execution_result, count).
    rows = [("restart_service", "failure", 5)]
    # success-count lookup when failure is seen → scalar().
    success_rows = [(2,)]

    calls = [_ScalarsAll(rows), _ScalarsAll(success_rows)]
    db = _make_db(execute_side_effect=lambda stmt: calls.pop(0))

    result = _run_task_with_db(
        decision_tasks.mine_decision_patterns, db, str(tenant_id),
    )

    assert result["tenant_id"] == str(tenant_id)
    assert len(result["insights"]) == 1
    assert result["insights"][0]["failure_rate"] == round(5 / 7, 3)


def test_mine_all_iterates_every_tenant_and_aggregates():
    t1, t2 = uuid4(), uuid4()

    # First execute is the tenant list; subsequent are per-tenant queries.
    # Each tenant has one row (no failure), so the success-lookup path
    # is NOT taken.
    responses = [
        _ScalarsAll([(t1,), (t2,)]),                              # list tenants
        _ScalarsAll([("restart", "success", 4)]),                 # tenant 1 mine
        _ScalarsAll([("escalate", "success", 3)]),                # tenant 2 mine
    ]
    db = _make_db(execute_side_effect=lambda stmt: responses.pop(0))

    result = _run_task_with_db(decision_tasks.mine_decision_patterns, db, "all")
    assert result == {"tenants": 2, "insight_count": 2}


def test_mine_all_isolates_one_failing_tenant():
    """If tenant A's query blows up, tenant B should still get mined."""
    t1, t2 = uuid4(), uuid4()

    responses = [
        _ScalarsAll([(t1,), (t2,)]),
        _ScalarsAll([("restart", "success", 4)]),  # tenant 1 ok
        RuntimeError("boom"),                       # tenant 2 explodes
    ]

    def execute(stmt):
        item = responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    db = _make_db(execute_side_effect=execute)
    result = _run_task_with_db(decision_tasks.mine_decision_patterns, db, "all")
    # Both tenants counted even though one failed.
    assert result["tenants"] == 2
    # Only tenant 1's insight made it through.
    assert result["insight_count"] == 1


# ---------------------------------------------------------------------------
# calibrate_decision_confidence
# ---------------------------------------------------------------------------


def test_calibrate_single_tenant_buckets_correctly():
    tenant_id = uuid4()

    # (confidence, execution_result) pairs — bucket by 0.1 increments.
    rows = [
        (0.9, "success"),
        (0.9, "success"),
        (0.9, "failure"),
        (0.5, "success"),
        (0.5, "failure"),
    ]
    db = _make_db(execute_side_effect=lambda stmt: _ScalarsAll(rows))

    result = _run_task_with_db(
        decision_tasks.calibrate_decision_confidence, db, str(tenant_id),
    )

    buckets = {b["predicted_confidence"]: b for b in result["calibration"]}
    assert buckets[0.9]["total"] == 3
    assert buckets[0.9]["success"] == 2
    assert buckets[0.9]["observed_success_rate"] == round(2 / 3, 3)
    assert buckets[0.5]["total"] == 2
    assert buckets[0.5]["observed_success_rate"] == 0.5


def test_calibrate_all_fans_out_and_aggregates_bucket_count():
    t1, t2 = uuid4(), uuid4()

    responses = [
        _ScalarsAll([(t1,), (t2,)]),
        _ScalarsAll([(0.9, "success"), (0.5, "failure")]),  # 2 buckets for t1
        _ScalarsAll([(0.7, "success")]),                     # 1 bucket for t2
    ]
    db = _make_db(execute_side_effect=lambda stmt: responses.pop(0))

    result = _run_task_with_db(decision_tasks.calibrate_decision_confidence, db, "all")
    assert result == {"tenants": 2, "bucket_count": 3}


def test_calibrate_all_isolates_tenant_failures():
    t1, t2 = uuid4(), uuid4()

    responses = [
        _ScalarsAll([(t1,), (t2,)]),
        _ScalarsAll([(0.9, "success")]),
        RuntimeError("db hiccup"),
    ]

    def execute(stmt):
        item = responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    db = _make_db(execute_side_effect=execute)
    result = _run_task_with_db(decision_tasks.calibrate_decision_confidence, db, "all")
    assert result["tenants"] == 2
    assert result["bucket_count"] == 1


def test_calibrate_skips_decisions_with_null_confidence():
    tenant_id = uuid4()
    rows = [
        (0.8, "success"),
        (None, "success"),  # should be skipped
        (0.8, "failure"),
    ]
    db = _make_db(execute_side_effect=lambda stmt: _ScalarsAll(rows))
    result = _run_task_with_db(
        decision_tasks.calibrate_decision_confidence, db, str(tenant_id),
    )
    assert len(result["calibration"]) == 1
    assert result["calibration"][0]["total"] == 2


# ---------------------------------------------------------------------------
# Registration regression — make sure Celery Beat can actually schedule these.
# ---------------------------------------------------------------------------


def test_beat_schedule_includes_decision_analytics():
    from contextedge.workers.celery_app import celery_app

    schedule = celery_app.conf.beat_schedule
    assert "calibrate-decision-confidence-daily" in schedule
    assert "mine-decision-patterns-daily" in schedule
    assert (
        schedule["calibrate-decision-confidence-daily"]["task"]
        == "evaluation.calibrate_decision_confidence"
    )


def test_tasks_registered_with_evaluation_names():
    """The task name prefix determines queue routing — an unnamed
    task falls into the default queue and breaks Beat."""
    from contextedge.workers.celery_app import celery_app

    names = set(celery_app.tasks.keys())
    assert "evaluation.calibrate_decision_confidence" in names
    assert "evaluation.mine_decision_patterns" in names
