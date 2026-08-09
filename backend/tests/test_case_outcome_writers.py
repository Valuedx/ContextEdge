"""The outcome/fix flywheel gets writers: schema + projection existed,
production writers did not — the learn-from-outcome loop only read."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from contextedge.models.case_outcome import (
    CaseOutcome,
    CaseOutcomeFixPattern,
    CaseStateTransition,
)
from contextedge.services.case_outcome_service import (
    get_case_history,
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


def _db_with_valid_fixes(added: list, *fix_ids):
    db = _db(added)
    db.execute = AsyncMock(
        return_value=SimpleNamespace(all=lambda: [(f,) for f in fix_ids])
    )
    return db


@pytest.mark.asyncio
async def test_fix_results_link_and_malformed_entries_skip():
    added: list = []
    fix_id = uuid4()
    await record_case_outcome(
        _db_with_valid_fixes(added, fix_id),
        uuid4(), SimpleNamespace(id=uuid4(), created_at=None),
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
async def test_foreign_fix_pattern_ids_never_accrue_statistics():
    """A fix id from another tenant (or thin air) must not link."""
    added: list = []
    await record_case_outcome(
        _db_with_valid_fixes(added),  # tenant owns none of the referenced ids
        uuid4(), SimpleNamespace(id=uuid4(), created_at=None),
        outcome_status="resolved",
        fix_results=[{"fix_pattern_id": str(uuid4()), "result": "successful"}],
    )
    assert [a for a in added if isinstance(a, CaseOutcomeFixPattern)] == []


@pytest.mark.asyncio
async def test_duplicate_fix_results_link_once_not_integrity_error():
    added: list = []
    fix_id = uuid4()
    await record_case_outcome(
        _db_with_valid_fixes(added, fix_id),
        uuid4(), SimpleNamespace(id=uuid4(), created_at=None),
        outcome_status="resolved",
        fix_results=[
            {"fix_pattern_id": str(fix_id), "result": "successful"},
            {"fix_pattern_id": str(fix_id), "result": "successful"},
            {"fix_pattern_id": str(fix_id), "result": "partial"},
        ],
    )
    links = [a for a in added if isinstance(a, CaseOutcomeFixPattern)]
    assert len(links) == 2  # (successful) deduped; (partial) is distinct
    assert {link.result for link in links} == {"successful", "partial"}


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


@pytest.mark.asyncio
async def test_case_history_serializes_timeline_and_outcomes():
    when = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)
    transition = SimpleNamespace(
        from_status=None, to_status="open", transition_reason=None,
        transitioned_by="ops", created_at=when,
    )
    outcome = SimpleNamespace(
        outcome_status="resolved", resolution_summary="fixed",
        confirmed_root_cause=None, successful_action=None,
        failed_actions=[], user_confirmed=True, mttr_minutes=42,
        closed_by="ops", closed_at=when,
    )
    db = MagicMock()
    t_res, o_res = MagicMock(), MagicMock()
    t_res.scalars.return_value.all.return_value = [transition]
    o_res.scalars.return_value.all.return_value = [outcome]
    db.execute = AsyncMock(side_effect=[t_res, o_res])
    history = await get_case_history(db, uuid4(), uuid4())
    assert history["transitions"] == [
        {
            "from_status": None, "to_status": "open", "reason": None,
            "transitioned_by": "ops", "at": when.isoformat(),
        }
    ]
    assert history["outcomes"][0]["outcome_status"] == "resolved"
    assert history["outcomes"][0]["mttr_minutes"] == 42.0


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
async def test_reclose_is_a_noop_for_history(mock_op, mock_inv):
    """Closing an already-closed session must not append closed->closed
    transitions or a second outcome."""
    from contextedge.services import session_service

    added: list = []
    session = _closable_session()
    session.status = "closed"  # already closed by an earlier call
    with patch.object(
        session_service, "get_resolution_session", new=AsyncMock(return_value=session)
    ):
        await session_service.close_resolution_session(
            _db(added), tenant_id=uuid4(), session_id=session.id,
            outcome={"outcome_status": "resolved"},
        )
    assert [a for a in added if isinstance(a, CaseStateTransition)] == []
    assert [a for a in added if isinstance(a, CaseOutcome)] == []


@pytest.mark.asyncio
async def test_fix_result_bool_confidence_is_not_a_confidence():
    added: list = []
    fix_id = uuid4()
    await record_case_outcome(
        _db_with_valid_fixes(added, fix_id),
        uuid4(), SimpleNamespace(id=uuid4(), created_at=None),
        outcome_status="resolved",
        fix_results=[
            {"fix_pattern_id": str(fix_id), "result": "successful", "confidence": True}
        ],
    )
    links = [a for a in added if isinstance(a, CaseOutcomeFixPattern)]
    assert links[0].confidence is None


@pytest.mark.asyncio
async def test_outcome_bearing_close_requires_knowledge_manager():
    """Anyone may close a session; asserting outcome facts is governed."""
    from fastapi import HTTPException

    from contextedge.api.v1.sessions import SessionCloseRequest, close_session

    user = MagicMock()
    user.require_role.side_effect = HTTPException(status_code=403, detail="Role required")
    with pytest.raises(HTTPException):
        await close_session(
            uuid4(), MagicMock(), user, SessionCloseRequest(outcome_status="resolved")
        )
    user.require_role.assert_called_once_with("knowledge_manager")


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
