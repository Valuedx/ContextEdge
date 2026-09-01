"""Tests for runtime quality filtering and staleness hooks."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from contextedge.ai import prompts as prompts_mod
from contextedge.ai.prompts import get_prompt, get_prompt_version, resolve_version
from contextedge.quality.states import STATE_FAIL, STATE_PASS, STATE_STALE
from contextedge.search.quality_filter import (
    assessment_excludes_runtime,
    filter_runtime_eligible,
)


def test_playbook_prompt_v10_is_default_without_v7_checklist():
    assert resolve_version("playbook") == "v10"
    system = get_prompt("playbook", "v10").system
    assert "coverage checklist" not in system
    assert "QUALITY CONTRACT" in system
    assert "checksum or antivirus verification" not in system


def test_v7_checklist_still_registered_for_baselines():
    assert "v7" in prompts_mod._REGISTRY["playbook"]
    assert "coverage checklist" in get_prompt_version("playbook", "v7").system


def test_assessment_excludes_fail_error_stale():
    fail = SimpleNamespace(
        overall_state=STATE_FAIL,
        stale_at=None,
        content_hash="abc",
    )
    assert assessment_excludes_runtime(fail, live_content_hash="abc")

    stale = SimpleNamespace(
        overall_state=STATE_PASS,
        stale_at="2026-01-01",
        content_hash="abc",
    )
    assert assessment_excludes_runtime(stale, live_content_hash="abc")

    pass_ok = SimpleNamespace(
        overall_state=STATE_PASS,
        stale_at=None,
        content_hash="abc",
    )
    assert not assessment_excludes_runtime(pass_ok, live_content_hash="abc")

    hash_mismatch = SimpleNamespace(
        overall_state=STATE_PASS,
        stale_at=None,
        content_hash="old",
    )
    assert assessment_excludes_runtime(hash_mismatch, live_content_hash="new")


@pytest.mark.asyncio
async def test_filter_runtime_eligible_drops_failed():
    tenant_id = uuid4()
    keep_id = uuid4()
    drop_id = uuid4()
    playbooks = {
        keep_id: SimpleNamespace(id=keep_id, current_version_id=None, title="ok"),
        drop_id: SimpleNamespace(id=drop_id, current_version_id=None, title="bad"),
    }
    assessments = {
        keep_id: SimpleNamespace(
            overall_state=STATE_PASS,
            stale_at=None,
            content_hash="h1",
            superseded_at=None,
        ),
        drop_id: SimpleNamespace(
            overall_state=STATE_STALE,
            stale_at="x",
            content_hash="h2",
            superseded_at=None,
        ),
    }

    with patch(
        "contextedge.search.quality_filter.assessments_for_playbooks",
        AsyncMock(return_value=assessments),
    ), patch(
        "contextedge.search.quality_filter.build_content",
        return_value={},
    ), patch(
        "contextedge.search.quality_filter.content_hash",
        side_effect=lambda _: "h1",
    ):
        kept = await filter_runtime_eligible(
            AsyncMock(),
            tenant_id,
            playbooks,
        )

    assert keep_id in kept
    assert drop_id not in kept


@pytest.mark.asyncio
async def test_signal_stale_for_evidence_links_playbooks():
    from contextedge.services.quality_staleness_hooks import signal_stale_for_evidence

    tenant_id = uuid4()
    evidence_id = uuid4()
    playbook_id = uuid4()

    with patch(
        "contextedge.services.quality_staleness_hooks.playbook_ids_linked_to_evidence",
        AsyncMock(return_value=[playbook_id]),
    ), patch(
        "contextedge.services.quality_staleness_hooks.signal_quality_stale",
        AsyncMock(return_value=1),
    ) as signal:
        count = await signal_stale_for_evidence(
            AsyncMock(),
            tenant_id,
            [evidence_id],
            origin="test",
        )

    assert count == 1
    signal.assert_awaited_once()
    assert signal.await_args.kwargs["reason"] == "source_changed"
