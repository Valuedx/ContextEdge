"""Post-action verification: did the fix hold? (migration 0036)"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import pytest

from contextedge.services.execution_verification_service import (
    DEFAULT_RECHECK_AFTER_SEC,
    MIN_RECHECK_FLOOR_SEC,
    _recheck_after_sec,
    _resolve_session_cis,
    verify_execution_run,
)

NOW = datetime(2026, 8, 1, 12, 0, 0, tzinfo=UTC)


def _run(**kw):
    return SimpleNamespace(
        id=uuid4(),
        tenant_id=kw.get("tenant_id", uuid4()),
        session_id=kw.get("session_id", uuid4()),
        playbook_version_id=uuid4(),
        status=kw.get("status", "completed"),
        outcome=kw.get("outcome", "success"),
        completed_at=kw.get("completed_at", NOW - timedelta(hours=1)),
        verification_status=None,
        verified_at=None,
        verification_details=None,
    )


def _version(policy=None):
    return SimpleNamespace(verification_policy=policy or {})


def _session(tenant_id, entities=None):
    return SimpleNamespace(
        id=uuid4(),
        tenant_id=tenant_id,
        entities=entities if entities is not None else ["vpn-gw-east-01"],
    )


def _scalars_result(values):
    result = Mock()
    result.scalars.return_value.all.return_value = values
    return result


def _rows_result(rows):
    result = Mock()
    result.all.return_value = rows
    return result


# --- policy helpers ---------------------------------------------------------


def test_recheck_delay_default_floor_and_garbage():
    assert _recheck_after_sec(None) == DEFAULT_RECHECK_AFTER_SEC
    assert _recheck_after_sec(_version({})) == DEFAULT_RECHECK_AFTER_SEC
    assert _recheck_after_sec(_version({"recheck_after_sec": 60})) == MIN_RECHECK_FLOOR_SEC
    assert _recheck_after_sec(_version({"recheck_after_sec": 7200})) == 7200
    assert _recheck_after_sec(_version({"recheck_after_sec": "soon"})) == DEFAULT_RECHECK_AFTER_SEC


# --- verdicts ---------------------------------------------------------------


def _first_result(row):
    result = Mock()
    result.first.return_value = row
    return result


def _count_result(value):
    result = Mock()
    result.scalar_one_or_none.return_value = value
    return result


def _db(version, session, execute_results, *, observable=True, confirmations=None):
    """Build a db double for the F9 sweep.

    The sweep now runs more queries than the absence check did: after CI
    resolution and the post-action scan it asks whether the CI has EVER
    reported (so silence can be told apart from recovery), then whether anyone
    confirmed the fix. ``observable`` and ``confirmations`` drive those two,
    and any query beyond them gets a permissive empty result so a test only
    has to state what it cares about.
    """
    added: list = []
    tail = [
        _first_result(object() if observable else None),
        _count_result(None if confirmations is None else max(confirmations, 1)),
        _count_result(confirmations),
    ]
    queue = list(execute_results) + tail

    async def execute(_stmt):
        if queue:
            return queue.pop(0)
        empty = Mock()
        empty.first.return_value = None
        empty.scalar_one_or_none.return_value = None
        empty.scalars.return_value.all.return_value = []
        empty.all.return_value = []
        return empty

    async def get(model, pk):
        name = getattr(model, "__name__", str(model))
        if "PlaybookVersion" in name:
            return version
        return session

    def add(obj):
        if getattr(obj, "id", None) is None:
            obj.id = uuid4()
        added.append(obj)

    db = SimpleNamespace(
        get=AsyncMock(side_effect=get),
        execute=AsyncMock(side_effect=execute),
        flush=AsyncMock(),
        add=add,
    )
    db.added = added
    return db


@pytest.mark.asyncio
async def test_skips_non_verifiable_outcomes_and_not_due_runs():
    tenant_id = uuid4()
    with patch(
        "contextedge.services.execution_verification_service.append_operational_event",
        AsyncMock(),
    ):
        failed_run = _run(tenant_id=tenant_id, outcome="failure")
        skipped = await verify_execution_run(SimpleNamespace(), tenant_id, failed_run, now=NOW)
        assert skipped == {"status": "skipped", "reason": "not_a_verifiable_outcome"}
        assert failed_run.verification_status is None

        fresh_run = _run(tenant_id=tenant_id, completed_at=NOW - timedelta(seconds=60))
        db = _db(_version(), None, [])
        not_due = await verify_execution_run(db, tenant_id, fresh_run, now=NOW)
        assert not_due["status"] == "not_due"
        assert fresh_run.verification_status is None  # stays in the queue


@pytest.mark.asyncio
async def test_verified_when_no_post_action_signals():
    tenant_id = uuid4()
    run = _run(tenant_id=tenant_id)
    ci = SimpleNamespace(id=uuid4(), name="vpn-gw-east-01")
    db = _db(
        _version({"auto_close_on_success": True}),
        _session(tenant_id),
        [_scalars_result([ci]), _rows_result([])],  # CI resolution, no signals
    )

    with patch(
        "contextedge.services.execution_verification_service.append_operational_event",
        AsyncMock(),
    ) as event_mock:
        result = await verify_execution_run(db, tenant_id, run, now=NOW)

    assert result["status"] == "verified"
    assert run.verification_status == "verified"
    assert run.verified_at == NOW
    # F9: the verdict now says WHAT was checked. Absence still passes, but
    # only because this CI is known to report — see the inconclusive test.
    assert run.verification_details["assessment"] == "success"
    assert run.verification_details["checked_cis"] == ["vpn-gw-east-01"]
    statuses = {
        c["type"]: c["status"] for c in run.verification_details["criteria"]
    }
    assert statuses["incident_absence"] == "pass"
    assert statuses["alert_absence"] == "pass"
    event_types = [c.kwargs["event_type"] for c in event_mock.await_args_list]
    assert event_types == [
        "execution.verification_completed",
        "execution.auto_close_recommended",  # policy opted in; recommend only
    ]


@pytest.mark.asyncio
async def test_failed_when_incidents_or_alerts_continue():
    tenant_id = uuid4()
    run = _run(tenant_id=tenant_id)
    ci = SimpleNamespace(id=uuid4(), name="vpn-gw-east-01")
    post_rows = [
        (uuid4(), "incident:" + "a" * 32),
        (uuid4(), "em_alert_rollup:ci:2026-08-01"),
        (uuid4(), "em_alert_rollup:ci:2026-08-01"),
    ]
    db = _db(_version(), _session(tenant_id), [_scalars_result([ci]), _rows_result(post_rows)])

    with patch(
        "contextedge.services.execution_verification_service.append_operational_event",
        AsyncMock(),
    ) as event_mock:
        result = await verify_execution_run(db, tenant_id, run, now=NOW)

    assert result["status"] == "failed"
    assert run.verification_status == "failed"
    observed = {
        c["type"]: c["observed"] for c in run.verification_details["criteria"]
    }
    assert observed["incident_absence"]["count"] == 1
    assert observed["alert_absence"]["count"] == 2
    # A recurrence is the one failure with something to undo.
    assert run.verification_details["assessment"] == "rollback_required"
    # No auto-close recommendation on a failed verdict, ever.
    event_types = [c.kwargs["event_type"] for c in event_mock.await_args_list]
    assert event_types == ["execution.verification_completed"]


@pytest.mark.asyncio
async def test_unverifiable_without_session_or_cis():
    tenant_id = uuid4()

    with patch(
        "contextedge.services.execution_verification_service.append_operational_event",
        AsyncMock(),
    ):
        no_session = _run(tenant_id=tenant_id, session_id=None)
        db = _db(_version(), None, [])
        result = await verify_execution_run(db, tenant_id, no_session, now=NOW)
        assert result["status"] == "unverifiable"
        assert no_session.verification_status == "unverifiable"

        no_cis = _run(tenant_id=tenant_id)
        db = _db(_version(), _session(tenant_id, entities=[]), [])
        result = await verify_execution_run(db, tenant_id, no_cis, now=NOW)
        assert result["status"] == "unverifiable"
        # F9: it now says WHY it could not decide, per criterion, instead of
        # one opaque reason code.
        assert result["details"]["assessment"] == "inconclusive"
        assert all(
            c["status"] == "not_observable" for c in result["details"]["criteria"]
        )


@pytest.mark.asyncio
async def test_foreign_tenant_session_is_ignored():
    tenant_id = uuid4()
    run = _run(tenant_id=tenant_id)
    foreign_session = _session(uuid4())  # different tenant
    db = _db(_version(), foreign_session, [])

    with patch(
        "contextedge.services.execution_verification_service.append_operational_event",
        AsyncMock(),
    ):
        result = await verify_execution_run(db, tenant_id, run, now=NOW)

    assert result["status"] == "unverifiable"


# --- CI resolution ----------------------------------------------------------


@pytest.mark.asyncio
async def test_session_ci_resolution_is_bounded_and_type_safe():
    tenant_id = uuid4()
    session = SimpleNamespace(
        entities=["VPN-GW-EAST-01", {"not": "a-string"}, "  ", None] + [f"e{i}" for i in range(20)],
    )
    captured = []

    async def execute(stmt):
        captured.append(stmt)
        return _scalars_result([])

    await _resolve_session_cis(SimpleNamespace(execute=execute), tenant_id, session)

    compiled = str(captured[0].compile(compile_kwargs={"literal_binds": False}))
    assert "lower" in compiled
    params = captured[0].compile().params
    terms = next(v for v in params.values() if isinstance(v, (list, tuple)))
    assert "vpn-gw-east-01" in terms  # lowercased
    assert len(terms) <= 10  # bounded despite 20+ inputs


# --- sweep ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sweep_filters_queue_and_contains_per_run_failures():
    from contextedge.workers.verification_tasks import _sweep

    tenant_id = uuid4()
    good_run = _run(tenant_id=tenant_id)
    bad_run = _run(tenant_id=tenant_id)
    captured_sql = []

    async def execute(stmt):
        captured_sql.append(str(stmt))
        return _scalars_result([good_run, bad_run])

    db = SimpleNamespace(execute=execute, commit=AsyncMock())

    async def verify(db_, tid, run, now):
        if run is bad_run:
            raise RuntimeError("boom")
        return {"status": "verified"}

    with patch(
        "contextedge.services.execution_verification_service.verify_execution_run",
        side_effect=verify,
    ):
        totals = await _sweep(db, str(tenant_id), limit=50)

    assert totals["verified"] == 1  # bad_run contained, sweep continued
    sql = captured_sql[0]
    assert "verification_status IS NULL" in sql
    assert "outcome IN" in sql
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_stale_alert_redelivery_does_not_fail_verification():
    """Alerts that FIRED before the fix but re-delivered after (state
    changes, closing storms) must not produce a false failure — the
    batch's own last_event_time decides."""
    tenant_id = uuid4()
    run = _run(tenant_id=tenant_id, completed_at=NOW - timedelta(hours=1))
    ci = SimpleNamespace(id=uuid4(), name="vpn-gw-east-01")
    alert_evidence_id = uuid4()
    post_rows = [(alert_evidence_id, "em_alert_rollup:ci:2026-08-01")]

    evidence = SimpleNamespace(id=alert_evidence_id, raw_object_ref=uuid4())
    raw = SimpleNamespace(id=evidence.raw_object_ref)
    version = _version()
    session = _session(tenant_id)

    async def get(model, pk):
        name = getattr(model, "__name__", str(model))
        if "PlaybookVersion" in name:
            return version
        if "ResolutionSession" in name:
            return session
        if "RawEvidenceObject" in name:
            return raw
        return evidence

    db = _db(
        version,
        session,
        [_scalars_result([ci]), _rows_result(post_rows)],
    )
    db.get = AsyncMock(side_effect=get)

    # Alerts fired 3h before completion — re-delivered batch, not new trouble.
    stale_payload = {"last_event_time": (NOW - timedelta(hours=4)).strftime("%Y-%m-%d %H:%M:%S")}
    with (
        patch(
            "contextedge.services.artifact_extraction_service.load_raw_payload",
            AsyncMock(return_value=stale_payload),
        ),
        patch(
            "contextedge.services.execution_verification_service.append_operational_event",
            AsyncMock(),
        ),
    ):
        result = await verify_execution_run(db, tenant_id, run, now=NOW)

    assert result["status"] == "verified"
    observed = {c["type"]: c["observed"] for c in run.verification_details["criteria"]}
    assert observed["alert_absence"]["count"] == 0


@pytest.mark.asyncio
async def test_genuinely_new_alerts_still_fail_verification():
    tenant_id = uuid4()
    run = _run(tenant_id=tenant_id, completed_at=NOW - timedelta(hours=1))
    ci = SimpleNamespace(id=uuid4(), name="vpn-gw-east-01")
    alert_evidence_id = uuid4()

    evidence = SimpleNamespace(id=alert_evidence_id, raw_object_ref=uuid4())
    raw = SimpleNamespace(id=evidence.raw_object_ref)
    version = _version()
    session = _session(tenant_id)

    async def get(model, pk):
        name = getattr(model, "__name__", str(model))
        if "PlaybookVersion" in name:
            return version
        if "ResolutionSession" in name:
            return session
        if "RawEvidenceObject" in name:
            return raw
        return evidence

    db = _db(
        version,
        session,
        [
            _scalars_result([ci]),
            _rows_result([(alert_evidence_id, "em_alert_rollup:ci:2026-08-01")]),
        ],
    )
    db.get = AsyncMock(side_effect=get)

    fresh_payload = {"last_event_time": (NOW - timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S")}
    with (
        patch(
            "contextedge.services.artifact_extraction_service.load_raw_payload",
            AsyncMock(return_value=fresh_payload),
        ),
        patch(
            "contextedge.services.execution_verification_service.append_operational_event",
            AsyncMock(),
        ),
    ):
        result = await verify_execution_run(db, tenant_id, run, now=NOW)

    assert result["status"] == "failed"
    observed = {c["type"]: c["observed"] for c in run.verification_details["criteria"]}
    assert observed["alert_absence"]["count"] == 1
    # Alerts alone are not a recurrence of the incident, so there is nothing
    # obvious to undo — this fails without recommending a rollback.
    assert run.verification_details["assessment"] == "failed"
