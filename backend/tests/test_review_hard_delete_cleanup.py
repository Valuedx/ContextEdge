"""Regression tests for the hard-delete orphan cleanup (F-18, F-20).

The cleanup_tasks module reaps two classes of orphans that survive
a ``purge_archived_evidence(mode="hard_delete")`` pass: raw blobs /
rows in ``raw_evidence_objects`` (no FK to evidence_items) and
``graph_edges`` whose node ids point at deleted evidence (plain-UUID
columns, no FK either).

These tests exercise the helper functions directly against
SimpleNamespace + AsyncMock stubs — enough to pin the behavior
without a live PG."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import pytest


class _ScalarsAll:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return SimpleNamespace(all=lambda: self._rows)

    def all(self):
        return self._rows


@pytest.mark.asyncio
async def test_reap_orphan_raw_blobs_deletes_s3_and_row():
    """A raw_evidence_objects row whose id is not referenced by any
    evidence_item.raw_object_ref gets its MinIO blob deleted and then
    the row dropped."""
    from contextedge.workers.cleanup_tasks import _reap_orphan_raw_blobs

    tenant_id = uuid4()
    orphan = SimpleNamespace(
        id=uuid4(),
        tenant_id=tenant_id,
        object_storage_key="raw/abc/def.json",
    )

    responses = [_ScalarsAll([orphan])]
    db = SimpleNamespace(
        execute=AsyncMock(side_effect=lambda stmt: responses.pop(0)),
        delete=AsyncMock(),
        flush=AsyncMock(),
    )

    with patch(
        "contextedge.workers.cleanup_tasks.delete_object",
        return_value=True,
    ) as delete_mock:
        stats = await _reap_orphan_raw_blobs(db, tenant_id, limit=10)

    delete_mock.assert_called_once_with("raw/abc/def.json")
    db.delete.assert_awaited_once_with(orphan)
    assert stats == {"blob_count": 1, "raw_row_count": 1}


@pytest.mark.asyncio
async def test_reap_orphan_raw_blobs_skips_row_on_blob_error():
    """If MinIO fails to delete the blob, the row stays — a retry on
    the next tick can try again rather than orphaning the key."""
    from contextedge.workers.cleanup_tasks import _reap_orphan_raw_blobs

    tenant_id = uuid4()
    orphan = SimpleNamespace(
        id=uuid4(),
        tenant_id=tenant_id,
        object_storage_key="raw/boom/key.json",
    )

    responses = [_ScalarsAll([orphan])]
    db = SimpleNamespace(
        execute=AsyncMock(side_effect=lambda stmt: responses.pop(0)),
        delete=AsyncMock(),
        flush=AsyncMock(),
    )

    with patch(
        "contextedge.workers.cleanup_tasks.delete_object",
        side_effect=RuntimeError("minio down"),
    ):
        stats = await _reap_orphan_raw_blobs(db, tenant_id, limit=10)

    # Row is preserved for next-tick retry; neither counter bumps.
    db.delete.assert_not_awaited()
    assert stats == {"blob_count": 0, "raw_row_count": 0}


@pytest.mark.asyncio
async def test_reap_orphan_raw_blobs_tolerates_missing_key():
    """A raw row with no object_storage_key (nothing was ever
    offloaded) should just drop the row, skip the blob step."""
    from contextedge.workers.cleanup_tasks import _reap_orphan_raw_blobs

    orphan = SimpleNamespace(id=uuid4(), tenant_id=uuid4(), object_storage_key=None)
    responses = [_ScalarsAll([orphan])]
    db = SimpleNamespace(
        execute=AsyncMock(side_effect=lambda stmt: responses.pop(0)),
        delete=AsyncMock(),
        flush=AsyncMock(),
    )

    with patch("contextedge.workers.cleanup_tasks.delete_object") as delete_mock:
        stats = await _reap_orphan_raw_blobs(db, orphan.tenant_id, limit=10)

    delete_mock.assert_not_called()
    db.delete.assert_awaited_once_with(orphan)
    assert stats == {"blob_count": 0, "raw_row_count": 1}


@pytest.mark.asyncio
async def test_reap_orphan_graph_edges_deletes_both_directions():
    """A graph_edge row is dropped if its source or target node of type
    ``evidence`` no longer exists. Helper calls delete twice (source
    side + target side)."""
    from contextedge.workers.cleanup_tasks import _reap_orphan_graph_edges

    tenant_id = uuid4()

    # First call: source-side delete claims 3 rows. Second call:
    # target-side delete claims 2. Total 5.
    source_result = Mock(rowcount=3)
    target_result = Mock(rowcount=2)
    results = [source_result, target_result]

    db = SimpleNamespace(
        execute=AsyncMock(side_effect=lambda stmt: results.pop(0)),
    )

    total = await _reap_orphan_graph_edges(db, tenant_id, limit=1000)

    assert total == 5
    assert db.execute.await_count == 2
