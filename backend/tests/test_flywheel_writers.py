from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from contextedge.api.v1 import negative_knowledge as nk_api
from contextedge.deps import CurrentUser
from contextedge.schemas.review import NegativeKnowledgeCreate
from contextedge.services.retrieval_feedback_service import record_feedback


def _user():
    return CurrentUser(
        user_id=uuid4(),
        tenant_id=uuid4(),
        email="km@example.com",
        roles=["knowledge_manager"],
    )


@pytest.mark.asyncio
async def test_create_negative_knowledge_writes_playbook_link():
    user = _user()
    playbook_id = uuid4()

    class _Result:
        def __init__(self, value):
            self._value = value

        def scalar_one_or_none(self):
            return self._value

    playbook = SimpleNamespace(id=playbook_id, tenant_id=user.tenant_id)
    added = []

    def capture_add(obj):
        added.append(obj)
        if getattr(obj, "id", None) is None:
            obj.id = uuid4()

    db = SimpleNamespace(
        add=capture_add,
        flush=AsyncMock(),
        refresh=AsyncMock(),
        execute=AsyncMock(side_effect=[_Result(playbook), _Result(None)]),
    )
    body = NegativeKnowledgeCreate(
        step_text="Do not reboot the core switch",
        playbook_id=playbook_id,
    )
    await nk_api.create_negative_knowledge(body, db, user)
    assert any(
        getattr(obj, "playbook_id", None) == playbook_id
        and getattr(obj, "negative_knowledge_id", None) is not None
        for obj in added
    )


@pytest.mark.asyncio
async def test_confirmed_feedback_writes_validated_fix_edge():
    tenant_id = uuid4()
    playbook_id = uuid4()
    session_id = uuid4()
    match_id = "m-1"
    match_record = SimpleNamespace(
        id=uuid4(),
        query_frame={"session_id": str(session_id)},
        ranked_results=[],
    )

    class _Result:
        def scalar_one_or_none(self):
            return match_record

    db = SimpleNamespace(add=lambda obj: setattr(obj, "id", uuid4()), flush=AsyncMock())
    added = []

    def capture_add(obj):
        added.append(obj)
        obj.id = uuid4()

    db.add = capture_add
    db.execute = AsyncMock(return_value=_Result())

    with patch(
        "contextedge.graph.builder.ensure_edge", new=AsyncMock()
    ) as ensure:
        feedback = await record_feedback(
            db,
            tenant_id=tenant_id,
            match_id=match_id,
            playbook_id=playbook_id,
            playbook_version_id=uuid4(),
            feedback_type="confirmed",
            details={},
            submitted_by=uuid4(),
        )

    assert feedback.feedback_type == "confirmed"
    ensure.assert_awaited()
    kwargs = ensure.await_args
    assert kwargs.args[2] == "session"
    assert kwargs.args[3] == session_id
    assert kwargs.args[4] == "playbook"
    assert kwargs.args[5] == playbook_id
    assert kwargs.args[6] == "validated_fix"


@pytest.mark.asyncio
async def test_partial_feedback_writes_partially_validated_fix():
    tenant_id = uuid4()
    playbook_id = uuid4()
    db = SimpleNamespace(add=lambda obj: setattr(obj, "id", uuid4()), flush=AsyncMock())

    def capture_add(obj):
        obj.id = uuid4()

    db.add = capture_add
    db.execute = AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: None))

    with patch(
        "contextedge.graph.builder.ensure_edge", new=AsyncMock()
    ) as ensure:
        await record_feedback(
            db,
            tenant_id=tenant_id,
            match_id=None,
            playbook_id=playbook_id,
            playbook_version_id=None,
            feedback_type="partial",
            details={},
            submitted_by=None,
        )

    assert ensure.await_args.args[6] == "partially_validated_fix"
    assert ensure.await_args.args[2] == "retrieval_feedback"
