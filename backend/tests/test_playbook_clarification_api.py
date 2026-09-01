"""HTTP handler tests for the clarification routes.

The behaviours worth pinning here are the ones a future refactor would break
silently:

- ``GET`` must not open a round. Reading a playbook must not spend an LLM call
  or change its history to record who looked.
- A mandatory question cannot be skipped, and the refusal must reach the caller
  as a 409 rather than being quietly dropped.
- A failed revision is a 422, not a 500: the request was well-formed and the
  round is still answerable, so the reviewer can edit an answer and retry.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from contextedge.api.v1 import playbooks
from contextedge.services import playbook_clarification_service as clarification
from tests.conftest import make_user


def _round(**over):
    base = {
        "id": uuid4(),
        "round_number": 1,
        "status": "open",
        "content_hash": "a" * 64,
        "assessment_id": None,
        "gap_count": 4,
        "question_count": 3,
        "mandatory_count": 1,
        "resolved_from_kb_count": 1,
        "resolved_from_context_count": 0,
        "kb_status": "ok",
        "prompt_name": "clarification_questions",
        "prompt_version": "v1",
        "generation_error": None,
        "applied_version_id": None,
        "opened_at": datetime.now(UTC),
        "closed_at": None,
        "notes": None,
    }
    base.update(over)
    return SimpleNamespace(**base)


def _question(**over):
    base = {
        "id": uuid4(),
        "gap_key": "k1",
        "gap_kind": "missing_required_action",
        "gap_origin": "finding",
        "target_kind": "playbook",
        "target_ref": None,
        "claim": "Restart the agent",
        "severity": "major",
        "question_text": "Which service must be restarted?",
        "why_it_matters": "The step cannot be followed without the name.",
        "obligation": "mandatory",
        "answer_kind": "text",
        "choices": [],
        "expected_format": None,
        "status": "open",
        "answer_text": None,
        "answer_source": None,
        "answer_provenance": None,
        "answered_at": None,
    }
    base.update(over)
    return SimpleNamespace(**base)


def _state(**over):
    base = {
        "playbook_id": uuid4(),
        "content_hash": "a" * 64,
        "round": _round(),
        "questions": [_question()],
        "matches_current_content": True,
        "has_live_round": True,
        "outstanding_mandatory": 1,
        "max_rounds": 5,
        "submission": {
            "ready": False,
            "blocked_reasons": ["mandatory_questions_outstanding"],
            "outstanding_mandatory": 1,
            "open_round_id": None,
            "open_round_status": "open",
            "quality": {"ready": False, "blocked_reason": "assessment_inconclusive"},
        },
    }
    base.update(over)
    return base


@pytest.mark.asyncio
async def test_get_clarification_returns_the_panel_shape():
    tenant_id = uuid4()
    playbook_id = uuid4()
    state = _state(playbook_id=playbook_id)

    with patch.object(
        playbooks, "_load_tenant_playbook",
        AsyncMock(return_value=SimpleNamespace(id=playbook_id, tenant_id=tenant_id)),
    ), patch.object(
        clarification, "clarification_state", AsyncMock(return_value=state)
    ):
        response = await playbooks.get_playbook_clarification(
            playbook_id=playbook_id, db=SimpleNamespace(), user=make_user(tenant_id=tenant_id)
        )

    assert response.playbook_id == playbook_id
    assert response.round.round_number == 1
    assert response.round.gap_count == 4
    assert len(response.questions) == 1
    assert response.questions[0].obligation == "mandatory"
    assert response.submission.ready is False
    assert response.submission.blocked_reasons == ["mandatory_questions_outstanding"]


@pytest.mark.asyncio
async def test_get_clarification_never_opens_a_round():
    """Reading a playbook must not spend a retrieval and two generation calls."""
    tenant_id = uuid4()
    playbook_id = uuid4()

    with patch.object(
        playbooks, "_load_tenant_playbook",
        AsyncMock(return_value=SimpleNamespace(id=playbook_id, tenant_id=tenant_id)),
    ), patch.object(
        clarification, "clarification_state", AsyncMock(return_value=_state(playbook_id=playbook_id))
    ), patch.object(
        clarification, "open_round", AsyncMock()
    ) as opener, patch.object(
        clarification, "apply_round", AsyncMock()
    ) as applier:
        await playbooks.get_playbook_clarification(
            playbook_id=playbook_id, db=SimpleNamespace(), user=make_user(tenant_id=tenant_id)
        )

    opener.assert_not_awaited()
    applier.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_clarification_404_for_a_foreign_tenant():
    with patch.object(
        playbooks, "_load_tenant_playbook",
        AsyncMock(side_effect=HTTPException(status_code=404, detail="Playbook not found")),
    ):
        with pytest.raises(HTTPException) as exc:
            await playbooks.get_playbook_clarification(
                playbook_id=uuid4(), db=SimpleNamespace(), user=make_user(tenant_id=uuid4())
            )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_reading_needs_no_role_but_opening_a_round_does():
    """A question is information a reviewer needs before deciding, so reading is
    open to tenant members — but opening a round writes rows and spends money."""
    tenant_id = uuid4()
    playbook_id = uuid4()
    playbook = SimpleNamespace(id=playbook_id, tenant_id=tenant_id)

    with patch.object(playbooks, "_load_tenant_playbook", AsyncMock(return_value=playbook)), \
         patch.object(clarification, "clarification_state",
                      AsyncMock(return_value=_state(playbook_id=playbook_id))):
        await playbooks.get_playbook_clarification(
            playbook_id=playbook_id, db=SimpleNamespace(), user=make_user(tenant_id=tenant_id)
        )

    with patch.object(playbooks, "_load_tenant_playbook", AsyncMock(return_value=playbook)), \
         patch.object(clarification, "open_round", AsyncMock()) as opener:
        with pytest.raises(HTTPException) as exc:
            await playbooks.open_clarification_round(
                playbook_id=playbook_id, db=SimpleNamespace(), user=make_user(tenant_id=tenant_id)
            )
    assert exc.value.status_code == 403
    opener.assert_not_awaited()


@pytest.mark.asyncio
async def test_opening_a_second_round_is_a_conflict():
    tenant_id = uuid4()
    playbook_id = uuid4()

    with patch.object(
        playbooks, "_load_tenant_playbook",
        AsyncMock(return_value=SimpleNamespace(id=playbook_id, tenant_id=tenant_id)),
    ), patch.object(
        clarification, "open_round",
        AsyncMock(side_effect=clarification.RoundAlreadyOpen("round 1 is still open")),
    ):
        with pytest.raises(HTTPException) as exc:
            await playbooks.open_clarification_round(
                playbook_id=playbook_id,
                db=SimpleNamespace(),
                user=make_user(["playbook_reviewer"], tenant_id=tenant_id),
            )
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "clarification_round_open"


@pytest.mark.asyncio
async def test_skipping_a_mandatory_question_is_refused_with_a_reason():
    """Silently dropping the skip would leave the reviewer believing they had
    disposed of the question, and the round un-appliable with no explanation."""
    from contextedge.schemas.playbook_clarification import (
        ClarificationAnswerInput,
        ClarificationAnswersRequest,
    )

    tenant_id = uuid4()
    playbook_id = uuid4()
    body = ClarificationAnswersRequest(
        answers=[ClarificationAnswerInput(question_id=uuid4(), skip=True)]
    )

    with patch.object(
        playbooks, "_load_tenant_playbook",
        AsyncMock(return_value=SimpleNamespace(id=playbook_id, tenant_id=tenant_id)),
    ), patch.object(
        clarification, "record_answers",
        AsyncMock(
            side_effect=clarification.ClarificationError(
                "a mandatory question cannot be skipped: Which service must be restarted?"
            )
        ),
    ):
        with pytest.raises(HTTPException) as exc:
            await playbooks.answer_clarification_questions(
                playbook_id=playbook_id,
                body=body,
                db=SimpleNamespace(),
                user=make_user(["playbook_reviewer"], tenant_id=tenant_id),
            )
    assert exc.value.status_code == 409
    assert "mandatory" in exc.value.detail["message"]


@pytest.mark.asyncio
async def test_applying_with_mandatory_outstanding_reports_how_many():
    from contextedge.schemas.playbook_clarification import ClarificationApplyRequest

    tenant_id = uuid4()
    playbook_id = uuid4()

    with patch.object(
        playbooks, "_load_tenant_playbook",
        AsyncMock(return_value=SimpleNamespace(id=playbook_id, tenant_id=tenant_id)),
    ), patch.object(
        clarification, "apply_round",
        AsyncMock(side_effect=clarification.MandatoryUnanswered(2)),
    ):
        with pytest.raises(HTTPException) as exc:
            await playbooks.apply_clarification_round(
                playbook_id=playbook_id,
                body=ClarificationApplyRequest(),
                db=SimpleNamespace(),
                user=make_user(["playbook_reviewer"], tenant_id=tenant_id),
            )
    assert exc.value.status_code == 409
    assert exc.value.detail["outstanding"] == 2


@pytest.mark.asyncio
async def test_a_failed_revision_is_422_so_the_round_can_be_retried():
    from contextedge.schemas.playbook_clarification import ClarificationApplyRequest

    tenant_id = uuid4()
    playbook_id = uuid4()

    with patch.object(
        playbooks, "_load_tenant_playbook",
        AsyncMock(return_value=SimpleNamespace(id=playbook_id, tenant_id=tenant_id)),
    ), patch.object(
        clarification, "apply_round",
        AsyncMock(
            side_effect=clarification.RevisionFailed(
                "revision returned no steps; nothing was written"
            )
        ),
    ):
        with pytest.raises(HTTPException) as exc:
            await playbooks.apply_clarification_round(
                playbook_id=playbook_id,
                body=ClarificationApplyRequest(),
                db=SimpleNamespace(),
                user=make_user(["playbook_reviewer"], tenant_id=tenant_id),
            )
    assert exc.value.status_code == 422
    assert exc.value.detail["code"] == "clarification_revision_failed"


@pytest.mark.asyncio
async def test_apply_reports_the_new_version_and_whether_the_loop_continues():
    from contextedge.schemas.playbook_clarification import ClarificationApplyRequest

    tenant_id = uuid4()
    playbook_id = uuid4()
    new_version = SimpleNamespace(id=uuid4(), semantic_version="0.3.0")
    applied = _round(status="applied", applied_version_id=new_version.id)
    following = _round(round_number=2, status="open")

    with patch.object(
        playbooks, "_load_tenant_playbook",
        AsyncMock(return_value=SimpleNamespace(id=playbook_id, tenant_id=tenant_id)),
    ), patch.object(
        clarification, "apply_round",
        AsyncMock(
            return_value={
                "applied_round": applied,
                "version": new_version,
                "answers_applied": 3,
                "next_round": following,
                "limit_reached": False,
            }
        ),
    ), patch.object(
        clarification, "submission_readiness",
        AsyncMock(
            return_value={
                "ready": False,
                "blocked_reasons": ["mandatory_questions_outstanding"],
                "outstanding_mandatory": 1,
                "open_round_id": None,
                "open_round_status": "open",
                "quality": {},
            }
        ),
    ), patch.object(playbooks, "log_audit_event", AsyncMock()):
        response = await playbooks.apply_clarification_round(
            playbook_id=playbook_id,
            body=ClarificationApplyRequest(),
            db=SimpleNamespace(),
            user=make_user(["playbook_reviewer"], tenant_id=tenant_id),
        )

    assert response.new_version_id == new_version.id
    assert response.new_semantic_version == "0.3.0"
    assert response.answers_applied == 3
    assert response.next_round.round_number == 2
    assert response.limit_reached is False


@pytest.mark.asyncio
async def test_apply_does_not_transition_the_playbook():
    """The loop reports readiness; a person presses Submit. A system that moves
    playbooks forward on its own judgement is what the support organisation
    rejected 28 playbooks for."""
    from contextedge.schemas.playbook_clarification import ClarificationApplyRequest

    tenant_id = uuid4()
    playbook_id = uuid4()
    new_version = SimpleNamespace(id=uuid4(), semantic_version="0.2.0")

    with patch.object(
        playbooks, "_load_tenant_playbook",
        AsyncMock(return_value=SimpleNamespace(id=playbook_id, tenant_id=tenant_id)),
    ), patch.object(
        clarification, "apply_round",
        AsyncMock(
            return_value={
                "applied_round": _round(status="applied"),
                "version": new_version,
                "answers_applied": 1,
                "next_round": _round(round_number=2, status="satisfied"),
                "limit_reached": False,
            }
        ),
    ), patch.object(
        clarification, "submission_readiness",
        AsyncMock(
            return_value={
                "ready": True,
                "blocked_reasons": [],
                "outstanding_mandatory": 0,
                "open_round_id": None,
                "open_round_status": None,
                "quality": {"ready": True, "blocked_reason": None},
            }
        ),
    ), patch.object(playbooks, "log_audit_event", AsyncMock()), patch.object(
        playbooks, "transition_playbook", AsyncMock()
    ) as transition:
        response = await playbooks.apply_clarification_round(
            playbook_id=playbook_id,
            body=ClarificationApplyRequest(),
            db=SimpleNamespace(),
            user=make_user(["playbook_reviewer"], tenant_id=tenant_id),
        )

    transition.assert_not_awaited()
    assert response.submission.ready is True


@pytest.mark.asyncio
async def test_abandon_404s_when_nothing_is_open():
    tenant_id = uuid4()
    with patch.object(
        playbooks, "_load_tenant_playbook",
        AsyncMock(return_value=SimpleNamespace(id=uuid4(), tenant_id=tenant_id)),
    ), patch.object(clarification, "abandon_round", AsyncMock(return_value=None)):
        with pytest.raises(HTTPException) as exc:
            await playbooks.abandon_clarification_round(
                playbook_id=uuid4(),
                db=SimpleNamespace(),
                user=make_user(["playbook_reviewer"], tenant_id=tenant_id),
            )
    assert exc.value.status_code == 404
