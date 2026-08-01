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

    # Handler now does a db.execute() lookup to derive a fallback domain_id
    # when the request body doesn't carry one. Mock execute() → scalar_one_or_none = None
    # so the handler's "no domain" branch runs and delay receives domain_id=None.
    scalar_result = Mock()
    scalar_result.scalar_one_or_none = Mock(return_value=None)
    db = SimpleNamespace(
        commit=AsyncMock(),
        execute=AsyncMock(return_value=scalar_result),
    )
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

    # Handler returns a typed TaskDispatchResponse now (review C-02):
    # status + task_id top-level, evidence_count + domain_id in detail.
    assert result.status == "reconstruction_queued"
    assert result.task_id == "task-123"
    assert result.detail == {"evidence_count": 1, "domain_id": None}
    audit_mock.assert_awaited_once()
    db.commit.assert_awaited_once()
    # delay now receives cluster_id (comma-joined ids) instead of the raw id,
    # plus the tenant and domain_id kwarg.
    # Manual reviewer triggers bypass the reconstruction debounce.
    delay_mock.assert_called_once_with(
        str(evidence_id), str(user.tenant_id), domain_id=None, settle=False,
    )


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
