"""The scheduled knowledge-dedup sweep (beat -> pattern.deduplicate_knowledge).

Wiring and guard behavior, not dedup logic — the passes themselves are
covered by test_episode_containment_dedup / test_episode_similarity_dedup,
and the entry-point composition by
test_the_dedup_entry_point_runs_both_new_passes.

The guard watches BOTH evidence inflow (bulk ingest) and episode creation
(reconstruction tail). The second condition is a scar: the 2026-08-18
12:29 sweep retired 446 drafts mid-tail because evidence had gone quiet
while reconstruction was still minting 40+ episodes per 10 minutes — some
of those clusters then paid a full re-synthesis.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from contextedge.workers.pattern_tasks import (
    DEDUP_ACTIVITY_THRESHOLD,
    EPISODE_ACTIVITY_THRESHOLD,
    _deduplicate_knowledge,
)

TENANT = uuid.uuid4()


def _count(n):
    result = MagicMock()
    result.scalar.return_value = n
    return result


def _db(activity, tenant_ids=None):
    """Canned results: optional tenant listing, then per tenant an
    (evidence, episodes) count pair in query order."""
    db = MagicMock()
    results = []
    if tenant_ids is not None:
        tenants = MagicMock()
        tenants.all.return_value = [(tid,) for tid in tenant_ids]
        results.append(tenants)
    for evidence, episodes in activity:
        results.append(_count(evidence))
        results.append(_count(episodes))
    db.execute = AsyncMock(side_effect=results)
    return db


@pytest.mark.asyncio
async def test_quiet_tenant_is_swept(monkeypatch):
    swept = AsyncMock(return_value={"merged_episodes": 3, "merged_patterns": 0})
    monkeypatch.setattr(
        "contextedge.services.pattern_service.deduplicate_patterns_and_playbooks",
        swept,
    )

    out = await _deduplicate_knowledge(_db([(0, 0)]), str(TENANT))

    swept.assert_awaited_once()
    assert out["deferred"] == 0
    assert out["results"][str(TENANT)]["merged_episodes"] == 3


@pytest.mark.asyncio
async def test_evidence_inflow_defers(monkeypatch):
    """During a bulk ingest the sweep must step aside: retiring drafts the
    next message burst regrows is pure churn."""
    swept = AsyncMock()
    monkeypatch.setattr(
        "contextedge.services.pattern_service.deduplicate_patterns_and_playbooks",
        swept,
    )

    out = await _deduplicate_knowledge(
        _db([(DEDUP_ACTIVITY_THRESHOLD + 1, 0)]), str(TENANT)
    )

    swept.assert_not_awaited()
    assert out["deferred"] == 1


@pytest.mark.asyncio
async def test_episode_churn_defers_even_with_quiet_evidence(monkeypatch):
    """The 12:29 regression: evidence quiet, reconstruction tail minting
    episodes — the sweep must NOT run and retire accounts mid-build."""
    swept = AsyncMock()
    monkeypatch.setattr(
        "contextedge.services.pattern_service.deduplicate_patterns_and_playbooks",
        swept,
    )

    out = await _deduplicate_knowledge(
        _db([(0, EPISODE_ACTIVITY_THRESHOLD + 1)]), str(TENANT)
    )

    swept.assert_not_awaited()
    assert out["deferred"] == 1


@pytest.mark.asyncio
async def test_all_fans_out_per_tenant_and_defers_only_the_busy_one(monkeypatch):
    """The guard is per tenant: one tenant's tail must not silence the
    hygiene sweep for everyone else."""
    quiet, busy = uuid.uuid4(), uuid.uuid4()
    db = _db(
        [(0, 0), (0, EPISODE_ACTIVITY_THRESHOLD + 100)],
        tenant_ids=[quiet, busy],
    )

    swept = AsyncMock(return_value={"merged_episodes": 0})
    monkeypatch.setattr(
        "contextedge.services.pattern_service.deduplicate_patterns_and_playbooks",
        swept,
    )

    out = await _deduplicate_knowledge(db, "all")

    assert out["tenants"] == 2
    assert out["deferred"] == 1
    assert list(out["results"]) == [str(quiet)]
    swept.assert_awaited_once_with(db, quiet)


# ---------------------------------------------------------------------------
# Registration regression — a task Beat cannot schedule is a no-op feature.
# ---------------------------------------------------------------------------


def test_beat_schedule_includes_knowledge_dedup():
    from contextedge.workers.celery_app import celery_app

    schedule = celery_app.conf.beat_schedule
    assert "deduplicate-knowledge-hourly" in schedule
    entry = schedule["deduplicate-knowledge-hourly"]
    assert entry["task"] == "pattern.deduplicate_knowledge"
    assert entry["args"] == ("all",)


def test_task_name_routes_to_the_pattern_queue():
    """`pattern.*` routes to the pattern queue, which the solo worker
    serializes — the sweep must never race clustering or playbook
    generation, both of which touch the same rows."""
    from contextedge.workers.celery_app import celery_app

    assert "pattern.deduplicate_knowledge" in celery_app.tasks
    route = celery_app.conf.task_routes.get("pattern.*")
    assert route == {"queue": "pattern"}
