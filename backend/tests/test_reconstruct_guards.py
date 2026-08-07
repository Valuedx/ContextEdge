"""E2e-run fixes: reconstruction race guard, singleton gate, Zoho
client-side filter, classification fast lane.

The governing incident: 8 identical episodes minted in 46 seconds by
concurrent reconstructs of one cluster (measured live), and 500 gate
calls starved ~40 minutes behind heavy extraction in the same queue.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from contextedge.workers import extraction_tasks


def _cluster(n: int):
    return SimpleNamespace(
        evidence_ids=[uuid.uuid4() for _ in range(n)],
        fingerprint=f"fp-test-{n}",
    )


def _db_with_lock(acquired: bool):
    db = MagicMock()
    result = MagicMock()
    result.scalar.return_value = acquired
    db.execute = AsyncMock(return_value=result)
    return db


@pytest.mark.asyncio
async def test_singleton_cluster_skips_without_llm():
    db = MagicMock()
    with patch(
        "contextedge.services.episode_cluster_service.resolve_episode_cluster",
        new=AsyncMock(return_value=_cluster(1)),
    ):
        out = await extraction_tasks._reconstruct(db, str(uuid.uuid4()), uuid.uuid4())
    assert out == {"status": "skipped_single_evidence"}
    db.execute.assert_not_called()


@pytest.mark.asyncio
async def test_manual_trigger_may_reconstruct_a_singleton():
    """settle=False (reviewer-triggered) bypasses the gate — the demo's
    per-ticket reconstruct endpoint depends on this."""
    db = _db_with_lock(acquired=False)  # then stops at the lock, fine
    with patch(
        "contextedge.services.episode_cluster_service.resolve_episode_cluster",
        new=AsyncMock(return_value=_cluster(1)),
    ):
        out = await extraction_tasks._reconstruct(
            db, str(uuid.uuid4()), uuid.uuid4(), settle=False
        )
    assert out != {"status": "skipped_single_evidence"}


@pytest.mark.asyncio
async def test_concurrent_reconstruct_loser_skips_without_llm():
    db = _db_with_lock(acquired=False)
    with patch(
        "contextedge.services.episode_cluster_service.resolve_episode_cluster",
        new=AsyncMock(return_value=_cluster(3)),
    ):
        out = await extraction_tasks._reconstruct(db, str(uuid.uuid4()), uuid.uuid4())
    assert out == {"status": "skipped_locked"}


def test_classification_routes_to_the_fast_lane():
    from contextedge.workers.celery_app import celery_app

    routes = celery_app.conf.task_routes
    assert routes["extraction.classify_relevance"] == {"queue": "default"}
    # Order matters: the explicit entry must precede the wildcard.
    keys = list(routes.keys())
    assert keys.index("extraction.classify_relevance") < keys.index("extraction.*")


def test_zoho_client_side_filter_verifies_rows():
    from contextedge.connectors.zoho_desk.connector import ZohoDeskConnector

    conn = ZohoDeskConnector.__new__(ZohoDeskConnector)
    conn.module_filters = {"tickets": {"status": "Resolved By Agent"}}
    assert conn._matches_module_filter("tickets", {"status": "Resolved By Agent"})
    assert conn._matches_module_filter("tickets", {"status": "resolved by agent"})
    assert not conn._matches_module_filter("tickets", {"status": "Open"})
    # Params with no row counterpart never drop rows.
    conn.module_filters = {"tickets": {"sortBy": "-modifiedTime"}}
    assert conn._matches_module_filter("tickets", {"status": "Open"})
    # No filter for the module: everything passes.
    assert conn._matches_module_filter("articles", {"status": "Draft"})
