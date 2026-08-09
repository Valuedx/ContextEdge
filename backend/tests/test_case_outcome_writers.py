"""The outcome/fix flywheel gets writers: schema + projection existed,
production writers did not — the learn-from-outcome loop only read."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from contextedge.models.case_outcome import (
    CaseOutcome,
    CaseOutcomeFixPattern,
    CaseStateTransition,
)
from contextedge.services.case_outcome_service import (
    record_case_outcome,
    record_case_transition,
)

_INVALIDATE = "contextedge.services.review_queue_service.invalidate_review_context"


def _db(added: list):
    return SimpleNamespace(
        add=lambda obj: added.append(obj),
        flush=AsyncMock(),
        refresh=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_transition_row_is_appended():
    added: list = []
    tenant_id, case_id = uuid4(), uuid4()
    t = await record_case_transition(
        _db(added), tenant_id, case_id,
        from_status="open", to_status="closed",
        reason="fixed", transitioned_by="ops@corp",
    )
    assert added == [t]
    assert t.from_status == "open" and t.to_status == "closed"
    assert t.transition_reason == "fixed"


@pytest.mark.asyncio
async def test_outcome_row_computes_mttr_from_session_timeline():
    added: list = []
    session = SimpleNamespace(
        id=uuid4(), created_at=datetime.now(UTC) - timedelta(minutes=90)
    )
    outcome = await record_case_outcome(
        _db(added), uuid4(), session,
        outcome_status="resolved",
        resolution_summary="Cert re-issued",
        successful_action="reissue_cert",
        user_confirmed=True,
    )
    assert outcome.outcome_status == "resolved"
    assert outcome.mttr_minutes is not None and 89 <= outcome.mttr_minutes <= 92
    assert outcome.user_confirmed is True


@pytest.mark.asyncio
async def test_invalid_outcome_status_is_refused():
    with pytest.raises(ValueError, match="outcome_status"):
        await record_case_outcome(
            _db([]), uuid4(), SimpleNamespace(id=uuid4(), created_at=None),
            outcome_status="totally_fine",
        )


@pytest.mark.asyncio
async def test_fix_results_link_and_malformed_entries_skip():
    added: list = []
    fix_id = uuid4()
    await record_case_outcome(
        _db(added), uuid4(), SimpleNamespace(id=uuid4(), created_at=None),
        outcome_status="workaround_applied",
        fix_results=[
            {"fix_pattern_id": str(fix_id), "result": "successful", "confidence": 0.8},
            {"fix_pattern_id": "not-a-uuid", "result": "successful"},
            {"fix_pattern_id": str(uuid4()), "result": "sideways"},
        ],
    )
    links = [a for a in added if isinstance(a, CaseOutcomeFixPattern)]
    assert len(links) == 1
    assert links[0].fix_pattern_id == fix_id and links[0].result == "successful"


@pytest.mark.asyncio
@patch("contextedge.services.session_service.append_operational_event", new_callable=AsyncMock)
@patch("contextedge.workers.review_queue_tasks.prefetch_review_context")
async def test_session_create_writes_the_opening_transition(mock_task, mock_op):
    from contextedge.services.session_service import create_resolution_session

    added: list = []
    session = await create_resolution_session(
        _db(added),
        tenant_id=uuid4(), initiated_by=uuid4(),
        symptoms=["s"], entities=["e"], external_case_ids=[],
    )
    transitions = [a for a in added if isinstance(a, CaseStateTransition)]
    assert len(transitions) == 1
    assert transitions[0].from_status is None
    assert transitions[0].to_status == "open"
    assert transitions[0].case_id == session.id


def _closable_session():
    return SimpleNamespace(
        id=uuid4(), status="open", closed_at=None,
        created_at=datetime.now(UTC) - timedelta(minutes=30),
    )


@pytest.mark.asyncio
@patch(_INVALIDATE, new_callable=AsyncMock)
@patch("contextedge.services.session_service.append_operational_event", new_callable=AsyncMock)
async def test_close_without_outcome_records_transition_only(mock_op, mock_inv):
    """An unstated outcome is unknown, not 'resolved'."""
    from contextedge.services import session_service

    added: list = []
    session = _closable_session()
    with patch.object(
        session_service, "get_resolution_session", new=AsyncMock(return_value=session)
    ):
        await session_service.close_resolution_session(
            _db(added), tenant_id=uuid4(), session_id=session.id
        )
    transitions = [a for a in added if isinstance(a, CaseStateTransition)]
    outcomes = [a for a in added if isinstance(a, CaseOutcome)]
    assert len(transitions) == 1
    assert transitions[0].from_status == "open" and transitions[0].to_status == "closed"
    assert outcomes == []


@pytest.mark.asyncio
@patch(_INVALIDATE, new_callable=AsyncMock)
@patch("contextedge.services.session_service.append_operational_event", new_callable=AsyncMock)
async def test_close_with_outcome_records_both(mock_op, mock_inv):
    from contextedge.services import session_service

    added: list = []
    session = _closable_session()
    with patch.object(
        session_service, "get_resolution_session", new=AsyncMock(return_value=session)
    ):
        await session_service.close_resolution_session(
            _db(added), tenant_id=uuid4(), session_id=session.id,
            outcome={
                "outcome_status": "resolved",
                "resolution_summary": "Pool size raised to 50",
                "confirmed_root_cause": "connection pool exhaustion",
            },
            closed_by="reviewer@corp",
        )
    outcomes = [a for a in added if isinstance(a, CaseOutcome)]
    assert len(outcomes) == 1
    assert outcomes[0].outcome_status == "resolved"
    assert outcomes[0].confirmed_root_cause == "connection pool exhaustion"
    assert outcomes[0].closed_by == "reviewer@corp"
    assert outcomes[0].case_id == session.id
