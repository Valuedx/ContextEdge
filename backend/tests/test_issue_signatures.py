"""B3 issue signatures + C2 recurrence: draft gate, key normalization,
dedupe, recurrence linking, approval discipline."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import pytest

from contextedge.models.case_bridge import EvidenceCaseMembership
from contextedge.models.issue_signature import EpisodeIssueSignature, IssueSignature
from contextedge.services.issue_signature_service import (
    IssueSignatureDraft,
    extract_issue_signature,
    signature_key_for,
)


class _NestedTx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


def test_draft_gate_clamps_and_nulls_unknown_vocab():
    draft = IssueSignatureDraft.model_validate(
        {
            "affected_capability": "wireless_connectivity",
            "failure_mode": "adapter_missing_after_resume",
            "environment": "the moon",
            "scope": "everywhere",
            "confidence": 3.0,
        }
    )
    assert draft.environment is None
    assert draft.scope is None
    assert draft.confidence == 1.0


def test_signature_key_normalizes_and_ignores_descriptive_fields():
    a = IssueSignatureDraft(
        affected_capability="Wireless Connectivity",
        failing_component="WiFi Adapter Driver",
        failure_mode="Adapter Missing After Resume",
        trigger_change="driver_update",
    )
    b = IssueSignatureDraft(
        affected_capability="wireless_connectivity",
        failing_component="wifi-adapter-driver",
        failure_mode="adapter_missing_after_resume",
        trigger_change="windows_patch",  # descriptive, not identity
        environment="production",
    )
    assert signature_key_for(a) == signature_key_for(b)
    assert signature_key_for(a) == (
        "wireless_connectivity|wifi_adapter_driver|adapter_missing_after_resume"
    )


def _episode(approved=True, evidence_ids=None):
    return SimpleNamespace(
        id=uuid4(),
        tenant_id=None,  # set by caller
        reviewer_state="approved" if approved else "pending_review",
        title="LPT001 Wi-Fi drops after sleep",
        root_cause_summary="Driver 23.40.0 regression",
        final_outcome="Rolled back driver",
        steps=[],
        evidence_ids=evidence_ids or [str(uuid4())],
    )


def _sig_db(
    *,
    episode,
    existing_link=None,
    existing_signature=None,
    prior_episode_link=None,
    prior_episode=None,
    prior_case=None,
    added=None,
):
    added = added if added is not None else []
    by_id = {}
    if prior_episode is not None:
        by_id[prior_episode.id] = prior_episode
    by_id[episode.id] = episode

    async def execute(stmt):
        text = str(stmt)
        result = Mock()
        if text.startswith("SELECT episode_steps."):
            result.scalars.return_value.all.return_value = []
            return result
        if text.startswith("SELECT episode_issue_signatures.episode_id"):
            result.scalar_one_or_none.return_value = prior_episode_link
            return result
        if text.startswith("SELECT episode_issue_signatures.id"):
            result.scalar_one_or_none.return_value = existing_link
            return result
        if text.startswith("SELECT issue_signatures."):
            result.scalar_one_or_none.return_value = existing_signature
            return result
        if text.startswith("SELECT evidence_case_memberships.canonical_case_id"):
            result.scalar_one_or_none.return_value = prior_case
            return result
        if text.startswith("SELECT evidence_case_memberships.id"):
            result.scalar_one_or_none.return_value = None
            return result
        result.scalar_one_or_none.return_value = None
        return result

    async def get(model, pk):
        return by_id.get(pk)

    return SimpleNamespace(
        execute=execute,
        get=AsyncMock(side_effect=get),
        add=added.append,
        flush=AsyncMock(),
        begin_nested=Mock(return_value=_NestedTx()),
    ), added


_GOOD_DRAFT = {
    "affected_capability": "wireless_connectivity",
    "failing_component": "wifi_adapter_driver",
    "failure_mode": "adapter_missing_after_resume",
    "trigger_change": "driver_update",
    "environment": "corporate_managed",
    "scope": "single_device",
    "confidence": 0.85,
}


@pytest.mark.asyncio
async def test_new_signature_created_for_approved_episode():
    tenant_id = uuid4()
    episode = _episode()
    episode.tenant_id = tenant_id
    db, added = _sig_db(episode=episode)

    with patch(
        "contextedge.services.issue_signature_service.llm_complete_json",
        AsyncMock(return_value=dict(_GOOD_DRAFT)),
    ):
        counts = await extract_issue_signature(db, tenant_id, episode.id)

    assert counts["status"] == "extracted"
    assert counts["is_new_signature"] is True
    assert counts["recurrence_links"] == 0
    sigs = [a for a in added if isinstance(a, IssueSignature)]
    links = [a for a in added if isinstance(a, EpisodeIssueSignature)]
    assert len(sigs) == 1 and len(links) == 1
    assert sigs[0].signature_key == (
        "wireless_connectivity|wifi_adapter_driver|adapter_missing_after_resume"
    )


@pytest.mark.asyncio
async def test_unapproved_episode_never_mints_signature():
    tenant_id = uuid4()
    episode = _episode(approved=False)
    episode.tenant_id = tenant_id
    db, added = _sig_db(episode=episode)

    llm = AsyncMock()
    with patch(
        "contextedge.services.issue_signature_service.llm_complete_json", llm
    ):
        counts = await extract_issue_signature(db, tenant_id, episode.id)

    assert counts["status"] == "skipped"
    assert not llm.called  # no LLM spend on unapproved stories
    assert added == []


@pytest.mark.asyncio
async def test_matching_signature_links_recurrence_to_prior_case():
    """The LPT121 case: same fingerprint as LPT001's episode months ago
    gets a recurrence pointer to the old case — never a merge."""
    tenant_id = uuid4()
    prior_case = uuid4()
    prior_episode = _episode()
    prior_episode.tenant_id = tenant_id
    episode = _episode()
    episode.tenant_id = tenant_id
    existing = IssueSignature(
        id=uuid4(),
        tenant_id=tenant_id,
        signature_key="wireless_connectivity|wifi_adapter_driver|adapter_missing_after_resume",
        affected_capability="wireless_connectivity",
        failure_mode="adapter_missing_after_resume",
        episode_count=1,
    )
    db, added = _sig_db(
        episode=episode,
        existing_signature=existing,
        prior_episode_link=prior_episode.id,
        prior_episode=prior_episode,
        prior_case=prior_case,
    )

    with patch(
        "contextedge.services.issue_signature_service.llm_complete_json",
        AsyncMock(return_value=dict(_GOOD_DRAFT)),
    ):
        counts = await extract_issue_signature(db, tenant_id, episode.id)

    assert counts["status"] == "extracted"
    assert counts["is_new_signature"] is False
    assert counts["recurrence_links"] == 1
    assert existing.episode_count == 2
    memberships = [a for a in added if isinstance(a, EvidenceCaseMembership)]
    assert len(memberships) == 1
    assert memberships[0].relationship_type == "recurrence"
    assert memberships[0].canonical_case_id == prior_case
    assert memberships[0].extraction_location == "issue_signature"


@pytest.mark.asyncio
async def test_invalid_draft_is_dropped_with_log():
    tenant_id = uuid4()
    episode = _episode()
    episode.tenant_id = tenant_id
    db, added = _sig_db(episode=episode)

    with patch(
        "contextedge.services.issue_signature_service.llm_complete_json",
        AsyncMock(return_value={"affected_capability": ""}),
    ):
        counts = await extract_issue_signature(db, tenant_id, episode.id)

    assert counts["status"] == "skipped"
    assert counts["reason"] == "invalid_draft"
    assert added == []


@pytest.mark.asyncio
async def test_already_extracted_episode_is_noop():
    tenant_id = uuid4()
    episode = _episode()
    episode.tenant_id = tenant_id
    db, added = _sig_db(episode=episode, existing_link=uuid4())

    llm = AsyncMock()
    with patch(
        "contextedge.services.issue_signature_service.llm_complete_json", llm
    ):
        counts = await extract_issue_signature(db, tenant_id, episode.id)

    assert counts["reason"] == "already_extracted"
    assert not llm.called
