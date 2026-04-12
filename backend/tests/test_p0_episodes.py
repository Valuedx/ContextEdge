from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from .conftest import make_user
from contextedge.api.v1 import episodes


@pytest.mark.asyncio
async def test_reconstruct_dispatches_celery():
    evidence_id = uuid4()
    user = make_user(roles=["domain_admin"])
    db = SimpleNamespace(commit=AsyncMock())
    task = SimpleNamespace(id="task-123")

    with patch.object(episodes, "log_audit_event", AsyncMock()) as audit_mock:
        with patch(
            "contextedge.workers.extraction_tasks.reconstruct_episode_task.delay",
            Mock(return_value=task),
        ) as delay_mock:
            result = await episodes.trigger_manual_reconstruction(
                episodes.ReconstructRequest(evidence_ids=[evidence_id]),
                db=db,
                user=user,
            )

    assert result == {
        "status": "reconstruction_queued",
        "evidence_count": 1,
        "task_id": "task-123",
    }
    audit_mock.assert_awaited_once()
    db.commit.assert_awaited_once()
    delay_mock.assert_called_once_with(str(evidence_id), str(user.tenant_id))


@pytest.mark.asyncio
async def test_reconstruct_requires_domain_admin():
    with pytest.raises(HTTPException) as exc_info:
        await episodes.trigger_manual_reconstruction(
            episodes.ReconstructRequest(evidence_ids=[uuid4()]),
            db=SimpleNamespace(),
            user=make_user(),
        )
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Role 'domain_admin' required"
