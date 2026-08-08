"""Resolution gate: defer synthesis for clusters with no solution signal.

The gate is a CLUSTER property, never an evidence filter — in
scattered-source deployments the problem and the fix arrive from
different systems, and dropping problem-side evidence would destroy
the join keys correlation needs. Default off; opting in must change
nothing until the knob is set.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from contextedge.config import settings
from contextedge.services.resolution_signal_service import (
    cluster_has_resolution_signal,
    text_has_resolution_signal,
)
from contextedge.workers import extraction_tasks

# --- detector: precision first ----------------------------------------------


def test_resolution_language_matches():
    for text in (
        "Issue was resolved after re-uploading web drivers via SysAdmin.",
        "Root cause identified: expired certificate on the F5 VIP.",
        "A workaround was applied until the patch lands.",
        "Status: Closed",
        "Working fine after restarting the iDoc queue.",
    ):
        assert text_has_resolution_signal(text), text


def test_problem_only_language_does_not_match():
    for text in (
        "Users report the VPN keeps disconnecting since this morning.",
        "We will fix this in the next release.",
        "Please share the logs so we can investigate further.",
        "Escalating to the plugin team for analysis.",
        "This needs to be resolved urgently.",
        None,
        "",
    ):
        assert not text_has_resolution_signal(text), text


@pytest.mark.asyncio
async def test_cluster_signal_found_in_summary_of_any_item():
    db = MagicMock()
    rows = MagicMock()
    rows.all.return_value = [
        ("VPN down for store 42", None, "Users cannot connect since 9am."),
        (None, "Resolved by re-issuing the device certificate.", None),
    ]
    db.execute = AsyncMock(return_value=rows)
    assert await cluster_has_resolution_signal(db, uuid.uuid4(), [uuid.uuid4()] * 2)


@pytest.mark.asyncio
async def test_cluster_without_signal_is_negative():
    db = MagicMock()
    rows = MagicMock()
    rows.all.return_value = [("VPN down", None, "Still investigating, no update.")]
    db.execute = AsyncMock(return_value=rows)
    assert not await cluster_has_resolution_signal(db, uuid.uuid4(), [uuid.uuid4()])


# --- gate wiring -------------------------------------------------------------


def _cluster(n: int = 3):
    return SimpleNamespace(evidence_ids=[uuid.uuid4() for _ in range(n)], fingerprint="fp-r")


@pytest.mark.asyncio
async def test_gate_off_is_default_and_changes_nothing():
    assert settings.episode_resolution_gate == "off"
    db = MagicMock()
    lock = MagicMock()
    lock.scalar.return_value = False  # stop at the advisory lock
    db.execute = AsyncMock(return_value=lock)
    with patch(
        "contextedge.services.episode_cluster_service.resolve_episode_cluster",
        new=AsyncMock(return_value=_cluster()),
    ):
        out = await extraction_tasks._reconstruct(db, str(uuid.uuid4()), uuid.uuid4())
    assert out == {"status": "skipped_locked"}  # gate never consulted


@pytest.mark.asyncio
async def test_cluster_mode_defers_unresolved_before_any_lock_or_llm():
    db = MagicMock()
    db.execute = AsyncMock()
    with (
        patch.object(settings, "episode_resolution_gate", "cluster"),
        patch(
            "contextedge.services.episode_cluster_service.resolve_episode_cluster",
            new=AsyncMock(return_value=_cluster()),
        ),
        patch(
            "contextedge.services.resolution_signal_service.cluster_has_resolution_signal",
            new=AsyncMock(return_value=False),
        ),
    ):
        out = await extraction_tasks._reconstruct(db, str(uuid.uuid4()), uuid.uuid4())
    assert out == {"status": "deferred_unresolved"}


@pytest.mark.asyncio
async def test_cluster_mode_proceeds_when_resolution_present():
    db = MagicMock()
    lock = MagicMock()
    lock.scalar.return_value = False  # proceed past gate, stop at lock
    db.execute = AsyncMock(return_value=lock)
    with (
        patch.object(settings, "episode_resolution_gate", "cluster"),
        patch(
            "contextedge.services.episode_cluster_service.resolve_episode_cluster",
            new=AsyncMock(return_value=_cluster()),
        ),
        patch(
            "contextedge.services.resolution_signal_service.cluster_has_resolution_signal",
            new=AsyncMock(return_value=True),
        ),
    ):
        out = await extraction_tasks._reconstruct(db, str(uuid.uuid4()), uuid.uuid4())
    assert out == {"status": "skipped_locked"}


@pytest.mark.asyncio
async def test_manual_trigger_bypasses_the_gate():
    """settle=False is a human asking — the gate must not argue."""
    db = MagicMock()
    lock = MagicMock()
    lock.scalar.return_value = False
    db.execute = AsyncMock(return_value=lock)
    with (
        patch.object(settings, "episode_resolution_gate", "cluster"),
        patch(
            "contextedge.services.episode_cluster_service.resolve_episode_cluster",
            new=AsyncMock(return_value=_cluster()),
        ),
        patch(
            "contextedge.services.resolution_signal_service.cluster_has_resolution_signal",
            new=AsyncMock(return_value=False),
        ) as gate,
    ):
        out = await extraction_tasks._reconstruct(db, str(uuid.uuid4()), uuid.uuid4(), settle=False)
    gate.assert_not_awaited()
    assert out == {"status": "skipped_locked"}


@pytest.mark.asyncio
async def test_gate_errors_fail_open():
    db = MagicMock()
    lock = MagicMock()
    lock.scalar.return_value = False
    db.execute = AsyncMock(return_value=lock)
    with (
        patch.object(settings, "episode_resolution_gate", "cluster"),
        patch(
            "contextedge.services.episode_cluster_service.resolve_episode_cluster",
            new=AsyncMock(return_value=_cluster()),
        ),
        patch(
            "contextedge.services.resolution_signal_service.cluster_has_resolution_signal",
            new=AsyncMock(side_effect=RuntimeError("gate broke")),
        ),
    ):
        out = await extraction_tasks._reconstruct(db, str(uuid.uuid4()), uuid.uuid4())
    assert out == {"status": "skipped_locked"}  # fell open, reached the lock
