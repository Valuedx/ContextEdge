from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import pytest
from fastapi import Response

from contextedge.api.v1 import playbooks as playbooks_api
from contextedge.schemas.playbook import PlaybookVersionForkRequest, PlaybookVersionResponse


def _payload(**over):
    base = {
        "id": uuid4(),
        "playbook_id": uuid4(),
        "semantic_version": "1.0.1",
        "trigger_conditions": {"symptoms": ["vpn down"]},
        "branching_logic": {},
        "inputs": [],
        "outputs": [],
        "steps": [{"order": 1, "text": "Renew the certificate"}],
        "rollback_notes": "Revert the cert",
        "evidence_refs": {"evidence_ids": [str(uuid4())]},
        "conflicts": None,
        "playbook_confidence": 0.9,
        "execution_confidence_guidance": "Re-check RADIUS",
        "verification_policy": {"auto_close_on_success": True, "recheck_after_sec": 1800},
        "published_at": None,
        "published_by": None,
        "created_at": datetime.now(UTC),
        "revision": 1,
        "updated_at": datetime.now(UTC),
        "derived_from_version_id": None,
        "created_by": None,
        "last_edited_by": None,
    }
    base.update(over)
    return base


@pytest.mark.asyncio
async def test_fork_copies_verification_policy_and_repoints_current():
    playbook_id = uuid4()
    source_id = uuid4()
    draft_id = uuid4()
    playbook = SimpleNamespace(
        id=playbook_id,
        tenant_id=uuid4(),
        lifecycle_state="approved",
        current_version_id=source_id,
        updated_at=None,
    )
    source = SimpleNamespace(
        id=source_id,
        playbook_id=playbook_id,
        published_at=datetime.now(UTC),
        steps=[{"text": "published step"}],
        **{k: v for k, v in _payload().items() if k not in {"id", "playbook_id", "published_at", "steps"}},
    )
    draft = SimpleNamespace(
        id=draft_id,
        playbook_id=playbook_id,
        published_at=None,
        semantic_version="1.0.2",
        derived_from_version_id=None,
        created_by=None,
        last_edited_by=None,
    )

    empty = Mock()
    empty.scalar_one_or_none.return_value = None
    db = SimpleNamespace(
        execute=AsyncMock(return_value=empty),
        flush=AsyncMock(),
        refresh=AsyncMock(),
    )
    user = SimpleNamespace(
        user_id=uuid4(),
        tenant_id=playbook.tenant_id,
        email="km@example.com",
        require_role=Mock(),
    )
    captured = {}

    async def fake_create(db, playbook, data):
        captured["data"] = data
        playbook.current_version_id = draft_id
        return draft

    with (
        patch.object(playbooks_api, "_load_tenant_playbook", AsyncMock(return_value=playbook)),
        patch.object(playbooks_api, "_load_playbook_version", AsyncMock(return_value=source)),
        patch.object(playbooks_api, "create_playbook_version", fake_create),
        patch.object(playbooks_api, "append_operational_event", AsyncMock()),
        patch.object(playbooks_api, "log_audit_event", AsyncMock()),
        patch(
            "contextedge.services.playbook_embedding.embed_playbook", AsyncMock()
        ) as embed_mock,
    ):
        out = await playbooks_api.fork_playbook_version_draft(
            playbook_id,
            source_id,
            db,
            user,
            Response(),
            PlaybookVersionForkRequest(edit_note="Need a safer restart"),
        )

    assert out is draft
    assert playbook.current_version_id == draft_id
    assert draft.derived_from_version_id == source_id
    assert captured["data"]["verification_policy"] == source.verification_policy
    assert captured["data"]["steps"] == source.steps
    embed_mock.assert_not_awaited()
    assert source.published_at is not None
    assert source.steps == [{"text": "published step"}]


@pytest.mark.asyncio
async def test_second_fork_returns_existing_draft():
    playbook_id = uuid4()
    draft_id = uuid4()
    playbook = SimpleNamespace(
        id=playbook_id,
        tenant_id=uuid4(),
        lifecycle_state="approved",
        current_version_id=uuid4(),
        updated_at=None,
    )
    existing = SimpleNamespace(id=draft_id, published_at=None)
    result = Mock()
    result.scalar_one_or_none.return_value = existing
    db = SimpleNamespace(execute=AsyncMock(return_value=result))
    user = SimpleNamespace(
        user_id=uuid4(),
        tenant_id=playbook.tenant_id,
        email="km@example.com",
        require_role=Mock(),
    )

    with (
        patch.object(playbooks_api, "_load_tenant_playbook", AsyncMock(return_value=playbook)),
        patch.object(playbooks_api, "_load_playbook_version", AsyncMock()),
        patch.object(playbooks_api, "create_playbook_version", AsyncMock()) as create_mock,
    ):
        response = Response()
        out = await playbooks_api.fork_playbook_version_draft(
            playbook_id, uuid4(), db, user, response, None
        )

    assert out is existing
    assert playbook.current_version_id == draft_id
    assert response.status_code == 200
    create_mock.assert_not_awaited()


def test_version_response_exposes_editing_fields():
    response = PlaybookVersionResponse(**_payload(published_at=None))
    assert response.is_editable is True
    assert response.revision == 1
    published = PlaybookVersionResponse(
        **_payload(published_at=datetime.now(UTC), revision=4)
    )
    assert published.is_editable is False
    assert published.revision == 4
