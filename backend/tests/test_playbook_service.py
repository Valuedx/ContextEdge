from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from contextedge.services.playbook_service import (
    DuplicateVersionError,
    InvalidTransitionError,
    _next_semantic_version,
    create_playbook_version,
    transition_playbook,
)


@pytest.mark.asyncio
async def test_transition_to_approved_publishes_current_version():
    playbook_id = uuid4()
    version_id = uuid4()
    actor_id = uuid4()
    playbook = SimpleNamespace(
        id=playbook_id,
        lifecycle_state="under_review",
        current_version_id=version_id,
        approver_user_id=None,
        last_validated_at=None,
    )
    version = SimpleNamespace(
        id=version_id,
        playbook_id=playbook_id,
        published_at=None,
        published_by=None,
    )
    db = SimpleNamespace(
        get=AsyncMock(return_value=version),
        add=Mock(),
        flush=AsyncMock(),
    )

    out = await transition_playbook(db, playbook, "approved", actor_id)

    assert out is playbook
    assert playbook.lifecycle_state == "approved"
    assert playbook.approver_user_id == actor_id
    assert playbook.last_validated_at is not None
    assert version.published_at is not None
    assert version.published_by == actor_id
    db.flush.assert_awaited()


@pytest.mark.asyncio
async def test_transition_to_approved_requires_current_version():
    playbook = SimpleNamespace(
        id=uuid4(),
        lifecycle_state="under_review",
        current_version_id=None,
        approver_user_id=None,
        last_validated_at=None,
    )
    db = SimpleNamespace(
        get=AsyncMock(),
        add=Mock(),
        flush=AsyncMock(),
    )

    with pytest.raises(InvalidTransitionError, match="current version"):
        await transition_playbook(db, playbook, "approved", uuid4())


def test_next_semantic_version_increments_highest_patch():
    assert _next_semantic_version(["0.1.0", "0.1.1", "1.4.9"]) == "1.4.10"


@pytest.mark.asyncio
async def test_create_playbook_version_rejects_duplicate_semantic_version():
    playbook = SimpleNamespace(id=uuid4(), current_version_id=None)
    result = Mock()
    result.scalars.return_value.all.return_value = ["0.1.0"]
    db = SimpleNamespace(
        execute=AsyncMock(return_value=result),
        add=Mock(),
        flush=AsyncMock(),
    )

    with pytest.raises(DuplicateVersionError, match="0.1.0"):
        await create_playbook_version(
            db,
            playbook,
            {
                "semantic_version": "0.1.0",
                "steps": [],
            },
        )


class _NestedTx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


@pytest.mark.asyncio
async def test_create_playbook_version_maps_integrity_error_to_duplicate():
    playbook = SimpleNamespace(id=uuid4(), current_version_id=None)
    query_result = Mock()
    query_result.scalars.return_value.all.return_value = []
    db = SimpleNamespace(
        execute=AsyncMock(return_value=query_result),
        add=Mock(),
        flush=AsyncMock(side_effect=IntegrityError("stmt", "params", Exception("dup"))),
        begin_nested=Mock(return_value=_NestedTx()),
    )

    with pytest.raises(DuplicateVersionError, match="0.1.0"):
        await create_playbook_version(
            db,
            playbook,
            {
                "semantic_version": "0.1.0",
                "steps": [],
            },
        )


@pytest.mark.asyncio
async def test_create_playbook_version_retries_generated_version_after_integrity_error():
    playbook = SimpleNamespace(id=uuid4(), current_version_id=None)
    execute_results = []
    first = Mock()
    first.scalars.return_value.all.return_value = ["0.1.0"]
    second = Mock()
    second.scalars.return_value.all.return_value = ["0.1.0", "0.1.1"]
    execute_results.extend([first, second])

    async def execute_side_effect(*_args, **_kwargs):
        return execute_results.pop(0)

    flush_calls = {"count": 0}

    async def flush_side_effect():
        flush_calls["count"] += 1
        if flush_calls["count"] == 1:
            raise IntegrityError("stmt", "params", Exception("dup"))

    db = SimpleNamespace(
        execute=AsyncMock(side_effect=execute_side_effect),
        add=Mock(),
        flush=AsyncMock(side_effect=flush_side_effect),
        begin_nested=Mock(return_value=_NestedTx()),
    )

    version = await create_playbook_version(
        db,
        playbook,
        {
            "steps": [],
        },
    )

    assert version.semantic_version == "0.1.2"
