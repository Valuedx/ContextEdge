from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import pytest

from contextedge.schemas.playbook import PlaybookStepPatch
from contextedge.services.playbook_editing import (
    PlaybookEditValidationError,
    normalize_steps,
    validate_steps,
)


def _patch(**kwargs) -> PlaybookStepPatch:
    return PlaybookStepPatch(**kwargs)


def test_unknown_keys_survive_a_text_edit():
    actor = uuid4()
    existing = [
        {
            "step_id": "s1",
            "text": "Restart the broker",
            "source_refs": [{"id": "kb-1"}],
            "grounding_status": "grounded",
            "vendor_flag": True,
            "order": 1,
        }
    ]
    steps, summary = normalize_steps(
        existing, [_patch(step_id="s1", text="Restart the broker gracefully")], actor
    )
    assert steps[0]["source_refs"] == [{"id": "kb-1"}]
    assert steps[0]["grounding_status"] == "grounded"
    assert steps[0]["vendor_flag"] is True
    assert steps[0]["human_edited"] is True
    assert steps[0]["edited_by"] == str(actor)
    assert summary["modified"] == ["s1"]


def test_new_step_is_labelled_human_authored():
    steps, summary = normalize_steps(
        [], [_patch(text="Check the license portal")], uuid4()
    )
    assert len(steps) == 1
    assert steps[0]["grounding_status"] == "non_grounded"
    assert steps[0]["step_classification"] == "human_authored"
    assert steps[0]["source_refs"] == []
    assert steps[0]["step_id"]
    assert steps[0]["order"] == 1
    assert summary["added"] == [steps[0]["step_id"]]


def test_positional_match_assigns_client_temp_ids():
    existing = [{"text": "First", "source_refs": [{"id": "ep-1"}]}]
    steps, _ = normalize_steps(
        existing, [_patch(step_id="temp-1", text="First, revised")], uuid4()
    )
    assert steps[0]["step_id"] == "temp-1"
    assert steps[0]["source_refs"] == [{"id": "ep-1"}]


def test_omitting_a_step_deletes_it():
    existing = [
        {"step_id": "keep", "text": "Keep me"},
        {"step_id": "drop", "text": "Drop me"},
    ]
    steps, summary = normalize_steps(
        existing, [_patch(step_id="keep", text="Keep me")], uuid4()
    )
    assert [s["step_id"] for s in steps] == ["keep"]
    assert summary["removed"] == ["drop"]


def test_reorder_rewrites_order_to_match_array_position():
    existing = [
        {"step_id": "a", "text": "A", "order": 1},
        {"step_id": "b", "text": "B", "order": 2},
    ]
    steps, summary = normalize_steps(
        existing,
        [_patch(step_id="b"), _patch(step_id="a")],
        uuid4(),
    )
    assert [s["step_id"] for s in steps] == ["b", "a"]
    assert [s["order"] for s in steps] == [1, 2]
    assert [s["index"] for s in steps] == [0, 1]
    assert summary["reordered"] is True


def test_duplicate_step_id_is_rejected():
    with pytest.raises(PlaybookEditValidationError, match="duplicate"):
        normalize_steps(
            [{"step_id": "a", "text": "A"}],
            [_patch(step_id="a"), _patch(step_id="a")],
            uuid4(),
        )


def test_validate_steps_allows_empty_draft_with_warning():
    result = validate_steps([])
    assert result["warnings"]


def test_validate_steps_rejects_blank_instruction():
    with pytest.raises(PlaybookEditValidationError, match="instruction"):
        validate_steps([{"step_id": "s1", "text": "  "}])


def test_validate_steps_rejects_over_four_thousand_chars():
    with pytest.raises(PlaybookEditValidationError, match="4000"):
        validate_steps([{"step_id": "s1", "text": "x" * 4001}])


def test_validate_steps_rejects_one_hundred_and_one_steps():
    steps = [{"step_id": f"s{i}", "text": f"Step {i}"} for i in range(101)]
    with pytest.raises(PlaybookEditValidationError, match="at most 100"):
        validate_steps(steps)


def test_validate_steps_rejects_unknown_safety_class():
    with pytest.raises(PlaybookEditValidationError, match="safety_class"):
        validate_steps(
            [{"step_id": "s1", "text": "Do it", "safety_class": "explode"}]
        )


def test_should_embed_draft_skips_approved_playbooks():
    from contextedge.api.v1.playbooks import _should_embed_draft

    assert _should_embed_draft(SimpleNamespace(lifecycle_state="approved")) is False
    assert _should_embed_draft(SimpleNamespace(lifecycle_state="candidate")) is True
    assert _should_embed_draft(SimpleNamespace(lifecycle_state="under_review")) is True


@pytest.mark.asyncio
async def test_patch_endpoint_does_not_embed_when_approved():
    from contextedge.api.v1 import playbooks as playbooks_api
    from contextedge.schemas.playbook import PlaybookVersionUpdate

    playbook_id = uuid4()
    version_id = uuid4()
    user_id = uuid4()
    playbook = SimpleNamespace(
        id=playbook_id,
        tenant_id=uuid4(),
        lifecycle_state="approved",
        current_version_id=version_id,
        updated_at=None,
    )
    version = SimpleNamespace(
        id=version_id,
        playbook_id=playbook_id,
        published_at=None,
        revision=1,
        steps=[{"step_id": "s1", "text": "Old", "source_refs": [{"id": "kb-1"}]}],
        rollback_notes=None,
        trigger_conditions={},
        branching_logic={},
        inputs=[],
        outputs=[],
        execution_confidence_guidance=None,
        playbook_confidence=0.8,
        verification_policy=None,
        semantic_version="1.0.1",
        last_edited_by=None,
        updated_at=datetime.now(UTC),
    )
    db = SimpleNamespace(
        execute=AsyncMock(),
        flush=AsyncMock(),
        refresh=AsyncMock(),
        get=AsyncMock(),
    )
    user = SimpleNamespace(
        user_id=user_id,
        tenant_id=playbook.tenant_id,
        email="km@example.com",
        require_role=Mock(),
    )
    body = PlaybookVersionUpdate(
        expected_revision=1,
        steps=[PlaybookStepPatch(step_id="s1", text="New wording")],
        edit_note="Clarify restart order",
    )

    with (
        patch.object(playbooks_api, "_load_tenant_playbook", AsyncMock(return_value=playbook)),
        patch.object(playbooks_api, "_load_playbook_version", AsyncMock(return_value=version)),
        patch.object(playbooks_api, "validate_step_bindings", AsyncMock()),
        patch.object(playbooks_api, "append_operational_event", AsyncMock()) as event_mock,
        patch.object(playbooks_api, "log_audit_event", AsyncMock()) as audit_mock,
        patch.object(playbooks_api, "flag_modified", Mock()),
        patch(
            "contextedge.services.playbook_embedding.embed_playbook", AsyncMock()
        ) as embed_mock,
        patch(
            "contextedge.schemas.playbook.PlaybookVersionResponse.model_validate",
            return_value=SimpleNamespace(edit_warnings=[]),
        ),
    ):
        await playbooks_api.update_playbook_version(
            playbook_id, version_id, body, db, user
        )

    embed_mock.assert_not_awaited()
    assert version.revision == 2
    assert version.steps[0]["source_refs"] == [{"id": "kb-1"}]
    event_mock.assert_awaited()
    audit_mock.assert_awaited()
    details = audit_mock.await_args.kwargs["details"]
    assert details["edit_note"] == "Clarify restart order"
    assert "text" not in str(details["summary"])
    assert version.last_edit_note == "Clarify restart order"


@pytest.mark.asyncio
async def test_patch_endpoint_embeds_candidate_current_version():
    from contextedge.api.v1 import playbooks as playbooks_api
    from contextedge.schemas.playbook import PlaybookVersionUpdate

    playbook_id = uuid4()
    version_id = uuid4()
    playbook = SimpleNamespace(
        id=playbook_id,
        tenant_id=uuid4(),
        lifecycle_state="candidate",
        current_version_id=version_id,
        updated_at=None,
    )
    version = SimpleNamespace(
        id=version_id,
        playbook_id=playbook_id,
        published_at=None,
        revision=1,
        steps=[{"step_id": "s1", "text": "Old"}],
        rollback_notes=None,
        trigger_conditions={},
        branching_logic={},
        inputs=[],
        outputs=[],
        execution_confidence_guidance=None,
        playbook_confidence=0.5,
        verification_policy=None,
        semantic_version="0.1.0",
        last_edited_by=None,
        updated_at=datetime.now(UTC),
    )
    db = SimpleNamespace(flush=AsyncMock(), refresh=AsyncMock())
    user = SimpleNamespace(
        user_id=uuid4(),
        tenant_id=playbook.tenant_id,
        email="km@example.com",
        require_role=Mock(),
    )
    body = PlaybookVersionUpdate(
        expected_revision=1,
        steps=[PlaybookStepPatch(step_id="s1", text="Updated candidate step")],
    )

    with (
        patch.object(playbooks_api, "_load_tenant_playbook", AsyncMock(return_value=playbook)),
        patch.object(playbooks_api, "_load_playbook_version", AsyncMock(return_value=version)),
        patch.object(playbooks_api, "validate_step_bindings", AsyncMock()),
        patch.object(playbooks_api, "append_operational_event", AsyncMock()),
        patch.object(playbooks_api, "log_audit_event", AsyncMock()),
        patch.object(playbooks_api, "flag_modified", Mock()),
        patch(
            "contextedge.services.playbook_embedding.embed_playbook", AsyncMock()
        ) as embed_mock,
        patch(
            "contextedge.schemas.playbook.PlaybookVersionResponse.model_validate",
            return_value=SimpleNamespace(edit_warnings=[]),
        ),
    ):
        await playbooks_api.update_playbook_version(
            playbook_id, version_id, body, db, user
        )

    embed_mock.assert_awaited_once()
    assert embed_mock.await_args.args[2] is version
