"""Tests for A1 (confidence filter/sort) and A5 (review-queue bundle)."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from contextedge.models.decision import Decision
from contextedge.services.review_queue_service import (
    build_review_context,
    derive_badge_level,
    _pick_top_decision,
    _success_rate,
)


# =========================================================================
# A1 — list_decisions confidence filter/sort
# =========================================================================


def test_list_sort_choices_rejects_invalid():
    """Invalid sort value raises ValueError so API can 400."""
    from contextedge.services.decision_trace_service import LIST_SORT_CHOICES, list_decisions
    import pytest as _pytest
    import asyncio

    assert "confidence_desc" in LIST_SORT_CHOICES
    assert "confidence_asc" in LIST_SORT_CHOICES
    assert "created_desc" in LIST_SORT_CHOICES

    async def _run():
        db = SimpleNamespace(execute=AsyncMock())
        await list_decisions(db, tenant_id=uuid4(), sort="bogus")

    with _pytest.raises(ValueError):
        asyncio.run(_run())


@pytest.mark.asyncio
async def test_list_decisions_confidence_filter_adds_where_clause():
    """When min/max_confidence are passed, the SELECT includes a confidence predicate."""
    from contextedge.services.decision_trace_service import list_decisions

    captured: dict = {}

    class _Exec:
        def scalars(self):
            return SimpleNamespace(all=lambda: [])

    async def _execute(stmt):
        captured["sql"] = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        return _Exec()

    db = SimpleNamespace(execute=_execute)

    await list_decisions(
        db,
        tenant_id=uuid4(),
        min_confidence=0.8,
        max_confidence=0.95,
        sort="confidence_desc",
    )

    sql = captured["sql"]
    assert "confidence >= 0.8" in sql
    assert "confidence <= 0.95" in sql
    assert "ORDER BY decisions.confidence DESC" in sql


@pytest.mark.asyncio
async def test_list_decisions_confidence_asc_uses_nulls_last():
    """Sorting by confidence_asc pushes NULL confidence rows to the end."""
    from contextedge.services.decision_trace_service import list_decisions

    captured: dict = {}

    class _Exec:
        def scalars(self):
            return SimpleNamespace(all=lambda: [])

    async def _execute(stmt):
        captured["sql"] = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        return _Exec()

    db = SimpleNamespace(execute=_execute)

    await list_decisions(db, tenant_id=uuid4(), sort="confidence_asc")

    assert "ORDER BY decisions.confidence ASC NULLS LAST" in captured["sql"]


# =========================================================================
# A5 — badge derivation
# =========================================================================


def test_derive_badge_level_thresholds():
    assert derive_badge_level(None) is None
    assert derive_badge_level(0.95) == "green"
    assert derive_badge_level(0.80) == "green"
    assert derive_badge_level(0.79) == "amber"
    assert derive_badge_level(0.50) == "amber"
    assert derive_badge_level(0.49) == "red"
    assert derive_badge_level(0.0) == "red"


# =========================================================================
# A5 — top decision picker
# =========================================================================


def _make_decision(
    *,
    status: str = "pending",
    confidence: float | None = None,
    created_at: datetime | None = None,
) -> Decision:
    d = Decision(
        id=uuid4(),
        tenant_id=uuid4(),
        decision_type="execute_playbook",
        agent_step="remediation",
        rationale_summary="test",
        status=status,
        confidence=confidence,
    )
    d.created_at = created_at or datetime(2026, 4, 19, 10, 0, tzinfo=timezone.utc)
    d.context_snapshot = {}
    return d


def test_pick_top_decision_prefers_pending_with_highest_confidence():
    d_old = _make_decision(confidence=0.9, created_at=datetime(2026, 4, 19, 8, 0, tzinfo=timezone.utc))
    d_new_low = _make_decision(confidence=0.3, created_at=datetime(2026, 4, 19, 12, 0, tzinfo=timezone.utc))
    d_completed = _make_decision(status="completed", confidence=0.99)
    top = _pick_top_decision([d_completed, d_old, d_new_low])
    assert top is d_old


def test_pick_top_decision_falls_back_to_latest_with_confidence_when_no_pending():
    d_completed_old = _make_decision(
        status="completed", confidence=0.6,
        created_at=datetime(2026, 4, 19, 8, 0, tzinfo=timezone.utc),
    )
    d_completed_new = _make_decision(
        status="completed", confidence=0.4,
        created_at=datetime(2026, 4, 19, 14, 0, tzinfo=timezone.utc),
    )
    top = _pick_top_decision([d_completed_old, d_completed_new])
    assert top is d_completed_new


def test_pick_top_decision_returns_none_for_empty_list():
    assert _pick_top_decision([]) is None


# =========================================================================
# A5 — success rate math
# =========================================================================


def test_success_rate_basic():
    assert _success_rate({"success": 3, "failure": 1}) == 0.75


def test_success_rate_excludes_unknown_results():
    # Only success/failure/partial/timeout/rejected count in the denominator.
    assert _success_rate({"success": 1, "some_bogus_result": 99}) == 1.0


def test_success_rate_none_when_no_outcomes():
    assert _success_rate({}) is None


# =========================================================================
# A5 — build_review_context composition
# =========================================================================


@pytest.mark.asyncio
@patch("contextedge.services.review_queue_service.list_operational_events", new_callable=AsyncMock, return_value=[])
@patch("contextedge.services.review_queue_service.list_execution_runs", new_callable=AsyncMock, return_value=[])
@patch("contextedge.services.review_queue_service.get_decision_effectiveness", new_callable=AsyncMock)
@patch("contextedge.services.review_queue_service.count_similar_decisions", new_callable=AsyncMock, return_value=143)
@patch("contextedge.services.review_queue_service.list_decisions", new_callable=AsyncMock)
@patch("contextedge.services.review_queue_service.get_resolution_session", new_callable=AsyncMock)
async def test_build_review_context_composes_bundle(
    mock_get_session, mock_list, mock_count, mock_eff, mock_runs, mock_events,
):
    tenant_id = uuid4()
    session_id = uuid4()

    session_stub = SimpleNamespace(
        id=session_id, tenant_id=tenant_id, status="open",
    )
    mock_get_session.return_value = session_stub

    top = _make_decision(confidence=0.92)
    top.context_snapshot = {"workflow": "vpn", "environment": "prod"}
    decisions = [top, _make_decision(confidence=0.3)]
    mock_list.return_value = decisions

    mock_eff.return_value = {
        "decision_type": "execute_playbook",
        "context_filters": {"workflow": "vpn", "environment": "prod"},
        "total": 87,
        "outcomes": {"success": 70, "failure": 10, "rejected": 7},
    }

    db = SimpleNamespace()
    bundle = await build_review_context(
        db, tenant_id=tenant_id, session_id=session_id,
    )

    assert bundle is not None
    assert bundle["session"] is session_stub
    assert bundle["top_decision"] is top
    assert bundle["top_decision_badge"] == {"score": 0.92, "level": "green"}
    assert bundle["similar"]["total_count"] == 143
    assert bundle["similar"]["decision_type"] == "execute_playbook"
    # success_rate = 70 / (70+10+7) == 70/87
    assert bundle["similar"]["success_rate"] == pytest.approx(70 / 87)
    assert bundle["decisions"] == decisions
    assert bundle["execution_runs"] == []
    assert bundle["recent_events"] == []

    # count_similar_decisions gets the context_filter slice, not full snapshot.
    count_kwargs = mock_count.call_args.kwargs
    assert count_kwargs["decision_type"] == "execute_playbook"
    assert count_kwargs["context_snapshot"] == {"workflow": "vpn", "environment": "prod"}


@pytest.mark.asyncio
@patch("contextedge.services.review_queue_service.get_resolution_session", new_callable=AsyncMock, return_value=None)
async def test_build_review_context_returns_none_for_missing_session(mock_get_session):
    db = SimpleNamespace()
    assert await build_review_context(db, tenant_id=uuid4(), session_id=uuid4()) is None


@pytest.mark.asyncio
@patch("contextedge.services.review_queue_service.list_operational_events", new_callable=AsyncMock, return_value=[])
@patch("contextedge.services.review_queue_service.list_execution_runs", new_callable=AsyncMock, return_value=[])
@patch("contextedge.services.review_queue_service.get_decision_effectiveness", new_callable=AsyncMock)
@patch("contextedge.services.review_queue_service.count_similar_decisions", new_callable=AsyncMock, return_value=0)
@patch("contextedge.services.review_queue_service.list_decisions", new_callable=AsyncMock, return_value=[])
@patch("contextedge.services.review_queue_service.get_resolution_session", new_callable=AsyncMock)
async def test_build_review_context_no_decisions_skips_similar(
    mock_get_session, mock_list, mock_count, mock_eff, mock_runs, mock_events,
):
    """When a session has no decisions, top_decision/similar/badge are all None
    and no similar-count lookup is performed."""
    mock_get_session.return_value = SimpleNamespace(id=uuid4(), tenant_id=uuid4())
    db = SimpleNamespace()

    bundle = await build_review_context(
        db, tenant_id=uuid4(), session_id=uuid4(),
    )

    assert bundle["top_decision"] is None
    assert bundle["top_decision_badge"] is None
    assert bundle["similar"] is None
    mock_count.assert_not_awaited()
    mock_eff.assert_not_awaited()


# =========================================================================
# count_similar_decisions SQL shape
# =========================================================================


@pytest.mark.asyncio
async def test_count_similar_decisions_returns_scalar():
    """count_similar_decisions emits COUNT(*) and returns the scalar int."""
    from contextedge.services.decision_trace_service import count_similar_decisions

    captured: dict = {}

    async def _execute(stmt):
        # Plain str() avoids JSONB literal-bind rendering issues.
        captured["sql"] = str(stmt)

        class _R:
            def scalar_one(self):
                return 143
        return _R()

    db = SimpleNamespace(execute=_execute)
    n = await count_similar_decisions(
        db,
        tenant_id=uuid4(),
        decision_type="execute_playbook",
        context_snapshot={"workflow": "vpn", "environment": "prod"},
    )
    assert n == 143
    sql_upper = captured["sql"].upper()
    assert "COUNT(*)" in sql_upper
    assert "DECISIONS" in sql_upper


@pytest.mark.asyncio
async def test_count_similar_decisions_type_only_when_no_context():
    """Without context_snapshot, count_similar_decisions still returns scalar."""
    from contextedge.services.decision_trace_service import count_similar_decisions

    async def _execute(stmt):
        class _R:
            def scalar_one(self):
                return 5
        return _R()

    db = SimpleNamespace(execute=_execute)
    n = await count_similar_decisions(
        db,
        tenant_id=uuid4(),
        decision_type="execute_playbook",
    )
    assert n == 5


# =========================================================================
# C1 — cache helpers (build_cache_key / read_cache / write_cache / invalidate)
# =========================================================================


def test_build_cache_key_is_tenant_scoped():
    """Same session_id under different tenants produces different keys so
    cross-tenant bleed is impossible at the cache layer."""
    from contextedge.services.review_queue_service import build_cache_key

    tid_a, tid_b = uuid4(), uuid4()
    sid = uuid4()
    key_a = build_cache_key(tid_a, sid)
    key_b = build_cache_key(tid_b, sid)

    assert key_a != key_b
    assert key_a.startswith("review_queue:")
    assert str(sid) in key_a


@pytest.mark.asyncio
async def test_write_cache_serializes_bundle_and_setex():
    """write_cache runs the bundle through ReviewQueueContext and SETEXs the
    JSON payload with the configured TTL."""
    from contextedge.services.review_queue_service import (
        REVIEW_CONTEXT_CACHE_TTL_SEC,
        write_cache,
    )

    tid = uuid4()
    sid = uuid4()

    session_payload = {
        "id": sid,
        "tenant_id": tid,
        "domain_id": None,
        "initiated_by": None,
        "status": "open",
        "symptoms": ["vpn down"],
        "entities": [],
        "external_case_ids": [],
        "notes": None,
        "closed_at": None,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
        "trace_events": [],
    }
    bundle = {
        "session": session_payload,
        "top_decision": None,
        "top_decision_badge": None,
        "similar": None,
        "decisions": [],
        "execution_runs": [],
        "recent_events": [],
    }

    captured = {}

    class _Redis:
        async def setex(self, key, ttl, value):
            captured["key"] = key
            captured["ttl"] = ttl
            captured["value"] = value

    await write_cache(_Redis(), tenant_id=tid, session_id=sid, bundle=bundle)

    assert captured["ttl"] == REVIEW_CONTEXT_CACHE_TTL_SEC
    assert captured["key"].endswith(f"{sid}")
    assert captured["key"].startswith("review_queue:")
    # Payload is JSON — must round-trip through ReviewQueueContext.
    import json
    parsed = json.loads(captured["value"])
    assert parsed["session"]["status"] == "open"
    assert parsed["top_decision"] is None


@pytest.mark.asyncio
async def test_read_cache_miss_returns_none():
    from contextedge.services.review_queue_service import read_cache

    class _Redis:
        async def get(self, key):
            return None

    assert await read_cache(_Redis(), tenant_id=uuid4(), session_id=uuid4()) is None


@pytest.mark.asyncio
async def test_read_cache_corrupt_payload_returns_none():
    """Corrupt cache entries must not 500 callers — return None and let the
    endpoint fall back to live compute."""
    from contextedge.services.review_queue_service import read_cache

    class _Redis:
        async def get(self, key):
            return "not valid json ¯\\_(ツ)_/¯"

    assert await read_cache(_Redis(), tenant_id=uuid4(), session_id=uuid4()) is None


@pytest.mark.asyncio
async def test_read_cache_hit_returns_validated_context():
    """A valid cached JSON payload round-trips through ReviewQueueContext
    without the endpoint needing to re-validate."""
    from contextedge.schemas.review_queue import ReviewQueueContext
    from contextedge.services.review_queue_service import read_cache

    tid = uuid4()
    sid = uuid4()
    now = datetime.now(timezone.utc)
    cached = ReviewQueueContext.model_validate(
        {
            "session": {
                "id": sid, "tenant_id": tid, "domain_id": None, "initiated_by": None,
                "status": "open", "symptoms": [], "entities": [], "external_case_ids": [],
                "notes": None, "closed_at": None, "created_at": now, "updated_at": now,
                "trace_events": [],
            },
            "top_decision": None,
            "top_decision_badge": None,
            "similar": None,
            "decisions": [],
            "execution_runs": [],
            "recent_events": [],
        },
    ).model_dump_json()

    class _Redis:
        async def get(self, key):
            return cached

    ctx = await read_cache(_Redis(), tenant_id=tid, session_id=sid)
    assert ctx is not None
    assert ctx.session.status == "open"
    assert ctx.top_decision is None


@pytest.mark.asyncio
async def test_invalidate_cache_calls_delete():
    from contextedge.services.review_queue_service import invalidate_cache

    captured = {}

    class _Redis:
        async def delete(self, key):
            captured["key"] = key

    tid, sid = uuid4(), uuid4()
    await invalidate_cache(_Redis(), tenant_id=tid, session_id=sid)

    assert captured["key"].startswith("review_queue:")
    assert str(tid) in captured["key"]
    assert str(sid) in captured["key"]


# =========================================================================
# C1 — session-creation prefetch hook
# =========================================================================


@pytest.mark.asyncio
@patch("contextedge.services.session_service.append_operational_event", new_callable=AsyncMock)
@patch("contextedge.workers.review_queue_tasks.prefetch_review_context")
async def test_create_resolution_session_enqueues_prefetch(
    mock_task, mock_op_event,
):
    """create_resolution_session fires prefetch_review_context.delay(tenant, session)
    so the review-queue cache is warm before the engineer opens the ticket."""
    from contextedge.services.session_service import create_resolution_session

    tenant_id = uuid4()
    initiated_by = uuid4()
    added: list = []

    db = SimpleNamespace(
        add=lambda obj: added.append(obj),
        flush=AsyncMock(),
        refresh=AsyncMock(),
    )

    session = await create_resolution_session(
        db,
        tenant_id=tenant_id,
        initiated_by=initiated_by,
        symptoms=["VPN auth failure"],
        entities=["vpn-gw-east-01"],
        external_case_ids=["JIRA-4521"],
    )

    assert session.status == "open"
    mock_task.delay.assert_called_once_with(str(tenant_id), str(session.id))


@pytest.mark.asyncio
@patch("contextedge.services.session_service.append_operational_event", new_callable=AsyncMock)
async def test_create_resolution_session_swallows_prefetch_enqueue_errors(mock_op_event):
    """If the Celery broker is unreachable, session creation still succeeds —
    the cache will just be cold on first read and the endpoint live-computes."""
    from contextedge.services.session_service import create_resolution_session

    db = SimpleNamespace(
        add=lambda obj: None,
        flush=AsyncMock(),
        refresh=AsyncMock(),
    )

    with patch(
        "contextedge.workers.review_queue_tasks.prefetch_review_context"
    ) as mock_task:
        mock_task.delay.side_effect = RuntimeError("broker down")

        # Should not raise.
        session = await create_resolution_session(
            db,
            tenant_id=uuid4(),
            initiated_by=uuid4(),
            symptoms=[],
            entities=[],
            external_case_ids=[],
        )
        assert session.status == "open"


# =========================================================================
# C1 follow-up — invalidation on mutation sites
# =========================================================================


@pytest.mark.asyncio
async def test_invalidate_review_context_noop_when_session_id_none():
    """Callers can invoke unconditionally without guarding on session link."""
    from contextedge.services.review_queue_service import invalidate_review_context

    with patch(
        "contextedge.services.review_queue_service.invalidate_cache",
        new_callable=AsyncMock,
    ) as mock_inner:
        await invalidate_review_context(uuid4(), None)
        mock_inner.assert_not_awaited()


@pytest.mark.asyncio
async def test_invalidate_review_context_swallows_redis_errors():
    """A degraded Redis must never bubble up into mutation code paths."""
    from contextedge.services.review_queue_service import invalidate_review_context

    class _BadRedis:
        async def delete(self, key):
            raise RuntimeError("redis down")

        async def aclose(self):
            pass

    with patch(
        "redis.asyncio.from_url",
        return_value=_BadRedis(),
    ):
        # Should not raise.
        await invalidate_review_context(uuid4(), uuid4())


@pytest.mark.asyncio
async def test_invalidate_review_context_opens_and_closes_client():
    """Short-lived client: opens via settings.redis_url, deletes, closes."""
    from contextedge.services.review_queue_service import invalidate_review_context

    client_closed = {"flag": False}

    class _Redis:
        async def delete(self, key):
            self._last_key = key

        async def aclose(self):
            client_closed["flag"] = True

    client = _Redis()
    with patch(
        "redis.asyncio.from_url",
        return_value=client,
    ):
        tid, sid = uuid4(), uuid4()
        await invalidate_review_context(tid, sid)

    assert client_closed["flag"] is True
    assert str(tid) in client._last_key
    assert str(sid) in client._last_key


# -------------------------------------------------------------------------
# Mutation sites fire invalidate_review_context
# -------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("contextedge.services.decision_trace_service.append_operational_event", new_callable=AsyncMock)
@patch("contextedge.services.decision_trace_service.append_trace_event", new_callable=AsyncMock)
@patch("contextedge.services.review_queue_service.invalidate_review_context", new_callable=AsyncMock)
async def test_create_decision_invalidates_cache_when_session_present(
    mock_invalidate, mock_trace, mock_op_event,
):
    from contextedge.services.decision_trace_service import create_decision

    tenant_id = uuid4()
    session_id = uuid4()

    db = SimpleNamespace(
        add=lambda obj: None,
        flush=AsyncMock(),
        refresh=AsyncMock(),
        execute=AsyncMock(),
    )

    await create_decision(
        db,
        tenant_id=tenant_id,
        decision_type="classify_issue",
        agent_step="diagnostics",
        rationale_summary="Test",
        session_id=session_id,
    )

    mock_invalidate.assert_awaited_once_with(tenant_id, session_id)


@pytest.mark.asyncio
@patch("contextedge.services.decision_trace_service.append_operational_event", new_callable=AsyncMock)
@patch("contextedge.services.review_queue_service.invalidate_review_context", new_callable=AsyncMock)
async def test_create_decision_invalidate_passes_none_when_no_session(
    mock_invalidate, mock_op_event,
):
    """With no session_id, we still call invalidate — the helper no-ops on None
    so callers stay uniform."""
    from contextedge.services.decision_trace_service import create_decision

    db = SimpleNamespace(
        add=lambda obj: None,
        flush=AsyncMock(),
        refresh=AsyncMock(),
        execute=AsyncMock(),
    )

    tenant_id = uuid4()
    await create_decision(
        db,
        tenant_id=tenant_id,
        decision_type="classify_issue",
        agent_step="diagnostics",
        rationale_summary="Test",
    )

    mock_invalidate.assert_awaited_once_with(tenant_id, None)


@pytest.mark.asyncio
@patch("contextedge.services.decision_trace_service.append_operational_event", new_callable=AsyncMock)
@patch("contextedge.services.decision_trace_service.link_decision_outcome", new_callable=AsyncMock)
@patch("contextedge.services.decision_trace_service.get_decision")
@patch("contextedge.services.review_queue_service.invalidate_review_context", new_callable=AsyncMock)
async def test_record_outcome_invalidates_using_decision_session_id(
    mock_invalidate, mock_get, mock_link_out, mock_op_event,
):
    from contextedge.models.decision import Decision
    from contextedge.services.decision_trace_service import record_outcome

    tenant_id = uuid4()
    session_id = uuid4()
    decision_id = uuid4()
    decision = Decision(
        id=decision_id,
        tenant_id=tenant_id,
        decision_type="restart_workflow",
        agent_step="remediation",
        rationale_summary="Test",
        status="pending",
        session_id=session_id,
    )
    mock_get.return_value = decision

    db = SimpleNamespace(
        add=lambda obj: None,
        flush=AsyncMock(),
        refresh=AsyncMock(),
    )

    await record_outcome(
        db,
        tenant_id=tenant_id,
        decision_id=decision_id,
        action_executed="restart_workflow",
        execution_result="success",
    )

    mock_invalidate.assert_awaited_once_with(tenant_id, session_id)


@pytest.mark.asyncio
@patch("contextedge.services.decision_trace_service.append_operational_event", new_callable=AsyncMock)
@patch("contextedge.services.decision_trace_service.link_decision_outcome", new_callable=AsyncMock)
@patch("contextedge.services.decision_trace_service.get_decision")
@patch("contextedge.services.review_queue_service.invalidate_review_context", new_callable=AsyncMock)
async def test_reject_decision_invalidates_using_decision_session_id(
    mock_invalidate, mock_get, mock_link_out, mock_op_event,
):
    from contextedge.models.decision import Decision, DecisionOption
    from contextedge.services.decision_trace_service import reject_decision

    tenant_id = uuid4()
    session_id = uuid4()
    decision_id = uuid4()
    chosen = DecisionOption(
        id=uuid4(), decision_id=decision_id, tenant_id=tenant_id,
        action="restart_workflow", selected=True,
    )
    decision = Decision(
        id=decision_id,
        tenant_id=tenant_id,
        decision_type="execute_playbook",
        agent_step="remediation",
        rationale_summary="Test",
        status="pending",
        human_override=False,
        session_id=session_id,
    )
    decision.options = [chosen]
    mock_get.return_value = decision

    db = SimpleNamespace(
        add=lambda obj: None,
        flush=AsyncMock(),
        refresh=AsyncMock(),
    )

    await reject_decision(
        db,
        tenant_id=tenant_id,
        decision_id=decision_id,
        code="wrong_diagnosis",
    )

    mock_invalidate.assert_awaited_once_with(tenant_id, session_id)


@pytest.mark.asyncio
@patch("contextedge.services.session_service.append_operational_event", new_callable=AsyncMock)
@patch("contextedge.services.review_queue_service.invalidate_review_context", new_callable=AsyncMock)
async def test_close_resolution_session_invalidates_own_session(
    mock_invalidate, mock_op_event,
):
    from contextedge.services.session_service import close_resolution_session
    from contextedge.models.session import ResolutionSession

    tenant_id = uuid4()
    session_id = uuid4()
    session_obj = ResolutionSession(
        id=session_id,
        tenant_id=tenant_id,
        status="open",
        symptoms=[],
        entities=[],
        external_case_ids=[],
    )

    with patch(
        "contextedge.services.session_service.get_resolution_session",
        new_callable=AsyncMock,
        return_value=session_obj,
    ):
        db = SimpleNamespace(flush=AsyncMock(), refresh=AsyncMock())
        await close_resolution_session(db, tenant_id=tenant_id, session_id=session_id)

    mock_invalidate.assert_awaited_once_with(tenant_id, session_id)
