from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import pytest

from contextedge.api.v1 import contradictions, episodes, playbooks, policy_assignments, sessions, threads
from contextedge.models.events import Notification
from contextedge.schemas.evidence import EpisodeStepUpdate
from contextedge.schemas.playbook import PlaybookRollbackRequest
from contextedge.schemas.review import ContradictionStatusUpdate, PolicyAssignmentRequest
from contextedge.services.notification_service import NotificationType, mark_notification_read, send_notification
from contextedge.services.source_service import rotate_source_credentials

from .conftest import make_user


class _ScalarOneOrNoneResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def scalar_one(self):
        return self._value


class _ScalarsResult:
    def __init__(self, values):
        self._values = values

    def scalars(self):
        return SimpleNamespace(all=lambda: self._values)


@pytest.mark.asyncio
async def test_list_sessions_route_uses_service():
    expected = [SimpleNamespace(id=uuid4())]
    with patch.object(sessions, "list_resolution_sessions", AsyncMock(return_value=expected)) as mock_list:
        result = await sessions.list_sessions(
            db=SimpleNamespace(),
            user=make_user(),
        )

    assert result == expected
    mock_list.assert_awaited_once()


@pytest.mark.asyncio
async def test_trigger_thread_hydration_queues_task():
    tenant_id = uuid4()
    thread_id = uuid4()
    source_id = uuid4()
    thread = SimpleNamespace(
        id=thread_id,
        tenant_id=tenant_id,
        source_id=source_id,
        external_thread_id="thr-123",
        hydration_status="pending",
    )
    db = SimpleNamespace(
        execute=AsyncMock(return_value=_ScalarOneOrNoneResult(thread)),
        flush=AsyncMock(),
    )
    fake_task = SimpleNamespace(id="task-1")

    with patch("contextedge.workers.hydration_tasks.hydrate_thread", new=SimpleNamespace(delay=Mock(return_value=fake_task))):
        result = await threads.trigger_thread_hydration(
            thread_id=thread_id,
            db=db,
            user=make_user(tenant_id=tenant_id),
        )

    assert thread.hydration_status == "queued"
    # Response is now a typed TaskDispatchResponse (review C-02).
    assert result.task_id == "task-1"
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_contradiction_status_marks_resolved():
    tenant_id = uuid4()
    contradiction_row = SimpleNamespace(
        id=uuid4(),
        tenant_id=tenant_id,
        resolution_status="unresolved",
        description="old",
        resolved_by=None,
    )
    db = SimpleNamespace(
        execute=AsyncMock(return_value=_ScalarOneOrNoneResult(contradiction_row)),
        flush=AsyncMock(),
        refresh=AsyncMock(),
    )
    user = make_user(roles=["knowledge_manager"], tenant_id=tenant_id)

    with patch.object(contradictions, "append_operational_event", AsyncMock()) as event_mock:
        result = await contradictions.update_contradiction_status(
            contradiction_id=contradiction_row.id,
            body=ContradictionStatusUpdate(resolution_status="resolved", description="handled"),
            db=db,
            user=user,
        )

    assert result is contradiction_row
    assert contradiction_row.resolution_status == "resolved"
    assert contradiction_row.description == "handled"
    assert contradiction_row.resolved_by == user.user_id
    event_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_notification_persists_and_can_mark_read():
    tenant_id = uuid4()
    user_id = uuid4()
    added: list[object] = []

    class _FakeDb:
        def __init__(self):
            self.flush = AsyncMock()
            self.refresh = AsyncMock()
            self._stored = None

        def add(self, obj):
            if getattr(obj, "id", None) is None:
                obj.id = uuid4()
            added.append(obj)
            self._stored = obj

        async def get(self, model, ident):
            if model is Notification and self._stored and ident == self._stored.id:
                return self._stored
            return None

    db = _FakeDb()

    await send_notification(
        db,
        tenant_id,
        user_id,
        NotificationType.CONTRADICTION_ALERT,
        "Contradiction detected",
        "KB disagrees with the playbook",
        metadata={"contradiction_id": "abc"},
    )

    notification = next(obj for obj in added if isinstance(obj, Notification))
    assert notification.notification_type == NotificationType.CONTRADICTION_ALERT.value
    assert notification.is_read is False

    updated = await mark_notification_read(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        notification_id=notification.id,
        is_read=True,
    )

    assert updated is notification
    assert notification.is_read is True
    assert notification.read_at is not None


@pytest.mark.asyncio
async def test_assign_policy_to_playbook_updates_approval_policy():
    tenant_id = uuid4()
    policy_id = uuid4()
    playbook_id = uuid4()
    playbook_row = SimpleNamespace(id=playbook_id, tenant_id=tenant_id, approval_policy_id=None)
    db = SimpleNamespace(
        execute=AsyncMock(return_value=_ScalarOneOrNoneResult(playbook_row)),
        flush=AsyncMock(),
    )

    with patch.object(policy_assignments, "assert_policy_assignment", AsyncMock()) as validate_mock:
        result = await policy_assignments.assign_policy(
            body=PolicyAssignmentRequest(
                resource_type="playbook",
                resource_id=playbook_id,
                policy_type="approval",
                policy_id=policy_id,
            ),
            db=db,
            user=make_user(roles=["domain_admin"], tenant_id=tenant_id),
        )

    assert playbook_row.approval_policy_id == policy_id
    assert result.policy_id == policy_id
    validate_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_rotate_source_credentials_replaces_active_credential():
    source_id = uuid4()
    old_cred = SimpleNamespace(source_id=source_id, status="active", rotated_at=None)
    source = SimpleNamespace(
        id=source_id,
        source_type="teams",
        config={},
        auth_type="oauth2",
        auth_status="connected",
    )
    added: list[object] = []
    db = SimpleNamespace(
        execute=AsyncMock(return_value=_ScalarsResult([old_cred])),
        add=lambda obj: added.append(obj),
        flush=AsyncMock(),
        refresh=AsyncMock(),
    )

    with (
        patch("contextedge.services.source_service.validate_source_credentials", AsyncMock(return_value=(True, "ok"))),
        patch("contextedge.services.source_service.encrypt_credentials", AsyncMock(return_value=b"encrypted")),
    ):
        new_cred = await rotate_source_credentials(
            db,
            source,
            credentials={"token": "new"},
        )

    assert old_cred.status == "rotated"
    assert old_cred.rotated_at is not None
    assert new_cred in added
    assert new_cred.status == "active"
    assert source.auth_status == "connected"


@pytest.mark.asyncio
async def test_get_playbook_version_diff_reports_changed_fields():
    tenant_id = uuid4()
    playbook_id = uuid4()
    base_id = uuid4()
    target_id = uuid4()
    playbook_row = SimpleNamespace(id=playbook_id, tenant_id=tenant_id, current_version_id=base_id)
    base = SimpleNamespace(
        id=base_id,
        playbook_id=playbook_id,
        semantic_version="1.0.0",
        trigger_conditions={},
        branching_logic={},
        inputs=[],
        outputs=[],
        steps=[{"text": "restart"}],
        rollback_notes=None,
        evidence_refs=[],
        conflicts=None,
        playbook_confidence=0.5,
        execution_confidence_guidance=None,
    )
    target = SimpleNamespace(
        id=target_id,
        playbook_id=playbook_id,
        semantic_version="1.1.0",
        trigger_conditions={},
        branching_logic={},
        inputs=[],
        outputs=[],
        steps=[{"text": "restart service safely"}],
        rollback_notes="undo",
        evidence_refs=[],
        conflicts=None,
        playbook_confidence=0.8,
        execution_confidence_guidance="manual check",
    )
    db = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                _ScalarOneOrNoneResult(playbook_row),
                _ScalarOneOrNoneResult(target),
            ]
        ),
        get=AsyncMock(return_value=base),
    )

    result = await playbooks.get_playbook_version_diff(
        playbook_id=playbook_id,
        version_id=target_id,
        db=db,
        user=make_user(tenant_id=tenant_id),
    )

    assert "steps" in result.changed_fields
    assert result.base_version_id == base_id
    assert result.target_version_id == target_id
    assert "restart service safely" in result.unified_diff


@pytest.mark.asyncio
async def test_update_episode_step_applies_patch():
    tenant_id = uuid4()
    episode_id = uuid4()
    step = SimpleNamespace(
        id=uuid4(),
        episode_id=episode_id,
        step_order=1,
        text="Old step",
        step_type="action",
        observation=None,
        result_state="unknown",
        failed_flag=False,
        successful_flag=False,
        extraction_confidence=0.4,
        evidence_refs=None,
    )
    db = SimpleNamespace(
        execute=AsyncMock(return_value=_ScalarOneOrNoneResult(step)),
        flush=AsyncMock(),
        refresh=AsyncMock(),
    )

    with patch.object(episodes, "log_audit_event", AsyncMock()) as audit_mock:
        result = await episodes.update_episode_step(
            episode_id=episode_id,
            step_id=step.id,
            body=EpisodeStepUpdate(text="New step", step_order=2),
            db=db,
            user=make_user(tenant_id=tenant_id),
        )

    assert result is step
    assert step.text == "New step"
    assert step.step_order == 2
    audit_mock.assert_awaited_once()
