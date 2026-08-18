"""The scheduled knowledge-dedup sweep (beat -> pattern.deduplicate_knowledge).

Wiring and guard behavior, not dedup logic — the passes themselves are
covered by test_episode_containment_dedup / test_episode_similarity_dedup,
and the entry-point composition by
test_the_dedup_entry_point_runs_both_new_passes.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from contextedge.workers.pattern_tasks import (
    DEDUP_ACTIVITY_THRESHOLD,
    _deduplicate_knowledge,
)

TENANT = uuid.uuid4()


def _db(recent_count: int, tenant_ids=None):
    """A db whose tenant listing and activity count are canned.

    Execute is called with: tenant SELECT (only for "all"), then per tenant
    one COUNT, and the entry point is monkeypatched — so canned results per
    call order are enough.
    """
    db = MagicMock()
    results = []
    if tenant_ids is not None:
        tenants = MagicMock()
        tenants.all.return_value = [(tid,) for tid in tenant_ids]
        results.append(tenants)
    for _ in tenant_ids or [TENANT]:
        count = MagicMock()
        count.scalar.return_value = recent_count
        results.append(count)
    db.execute = AsyncMock(side_effect=results)
    return db


@pytest.mark.asyncio
async def test_quiet_tenant_is_swept(monkeypatch):
    swept = AsyncMock(return_value={"merged_episodes": 3, "merged_patterns": 0})
    monkeypatch.setattr(
        "contextedge.services.pattern_service.deduplicate_patterns_and_playbooks",
        swept,
    )

    out = await _deduplicate_knowledge(_db(recent_count=0), str(TENANT))

    swept.assert_awaited_once()
    assert out["deferred"] == 0
    assert out["results"][str(TENANT)]["merged_episodes"] == 3


@pytest.mark.asyncio
async def test_busy_tenant_defers_without_touching_the_entry_point(monkeypatch):
    """During a bulk ingest the sweep must step aside: retiring drafts the
    next message burst regrows is pure churn, and the next hourly tick
    catches up once the tenant is quiet."""
    swept = AsyncMock()
    monkeypatch.setattr(
        "contextedge.services.pattern_service.deduplicate_patterns_and_playbooks",
        swept,
    )

    out = await _deduplicate_knowledge(
        _db(recent_count=DEDUP_ACTIVITY_THRESHOLD + 1), str(TENANT)
    )

    swept.assert_not_awaited()
    assert out["deferred"] == 1
    assert out["results"] == {}


@pytest.mark.asyncio
async def test_all_fans_out_per_tenant_and_defers_only_the_busy_one(monkeypatch):
    """The guard is per tenant: one tenant's backfill must not silence the
    hygiene sweep for everyone else."""
    quiet, busy = uuid.uuid4(), uuid.uuid4()

    db = MagicMock()
    tenants = MagicMock()
    tenants.all.return_value = [(quiet,), (busy,)]
    quiet_count = MagicMock()
    quiet_count.scalar.return_value = 0
    busy_count = MagicMock()
    busy_count.scalar.return_value = DEDUP_ACTIVITY_THRESHOLD + 100
    db.execute = AsyncMock(side_effect=[tenants, quiet_count, busy_count])

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
