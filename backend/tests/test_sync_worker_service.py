from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import pytest

from contextedge.services.sync_ingestion_queue import NormalizeEnqueueError
from contextedge.services.sync_worker_service import (
    _claim_pending_raw_ids_for_handoff,
    _commit_and_queue_normalization,
    _filter_already_normalized_raw_ids,
    _pending_handoff_raw_ids_from_errors,
    _pending_raw_ids_from_source_object,
)


def _rows_result(rows):
    result = Mock()
    result.all.return_value = rows
    return result


def _scalars_result(values):
    result = Mock()
    result.scalars.return_value.all.return_value = values
    return result


def _scalar_one_or_none_result(value):
    result = Mock()
    result.scalar_one_or_none.return_value = value
    return result


def test_pending_handoff_raw_ids_from_errors_dedupes_and_skips_invalid_values():
    raw_id = uuid4()
    other_raw_id = uuid4()

    assert _pending_handoff_raw_ids_from_errors(
        {
            "handoff": {
                "pending_raw_ids": [
                    str(raw_id),
                    "not-a-uuid",
                    str(raw_id),
                    None,
                    str(other_raw_id),
                ]
            }
        }
    ) == [raw_id, other_raw_id]


def test_pending_raw_ids_from_source_object_dedupes_and_skips_invalid_values():
    raw_id = uuid4()
    other_raw_id = uuid4()
    source_object = SimpleNamespace(
        metadata_extra={
            "pending_normalize_raw_ids": [
                str(raw_id),
                "not-a-uuid",
                str(raw_id),
                None,
                str(other_raw_id),
            ]
        }
    )

    assert _pending_raw_ids_from_source_object(source_object) == [raw_id, other_raw_id]


@pytest.mark.asyncio
async def test_filter_already_normalized_raw_ids_skips_exact_and_deduped_matches():
    tenant_id = uuid4()
    normalized_raw_id = uuid4()
    deduped_raw_id = uuid4()
    pending_raw_id = uuid4()
    db = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                _rows_result(
                    [
                        (normalized_raw_id, {"body": "normalized"}),
                        (deduped_raw_id, {"body": "deduped"}),
                        (pending_raw_id, {"body": "pending"}),
                    ]
                ),
                _scalars_result([normalized_raw_id]),
                _scalars_result(
                    [
                        "a18ba73f8bab915e4bd565c3e7d9cac68f0854de53cda0a7da014e9b6f2cbd9b"
                    ]
                ),
            ]
        )
    )

    pending = await _filter_already_normalized_raw_ids(
        db,
        tenant_id=tenant_id,
        raw_ids=[normalized_raw_id, deduped_raw_id, pending_raw_id],
    )

    assert pending == [pending_raw_id]


@pytest.mark.asyncio
async def test_claim_pending_raw_ids_for_handoff_clears_recovered_backlog_before_queue():
    tenant_id = uuid4()
    source_object_id = uuid4()
    recovered_raw_id = uuid4()
    new_raw_id = uuid4()
    source_object = SimpleNamespace(
        metadata_extra={
            "pending_normalize_raw_ids": [str(recovered_raw_id)],
            "connector_state": {"cursor": "abc"},
        }
    )
    db = SimpleNamespace(commit=AsyncMock(), flush=AsyncMock())

    with (
        patch(
            "contextedge.services.sync_worker_service._lock_source_object",
            AsyncMock(return_value=source_object),
        ),
        patch(
            "contextedge.services.sync_worker_service._filter_already_normalized_raw_ids",
            AsyncMock(return_value=[recovered_raw_id, new_raw_id]),
        ),
    ):
        # The claim now also returns the ingest-priority mode: the source
        # object is locked and loaded there, so the ordering step downstream
        # does not need a second query for it.
        pending_raw_ids, recovered_raw_ids, priority = await _claim_pending_raw_ids_for_handoff(
            db,
            tenant_id=tenant_id,
            source_object_id=source_object_id,
            new_raw_ids=[new_raw_id],
        )

    assert pending_raw_ids == [recovered_raw_id, new_raw_id]
    assert recovered_raw_ids == [recovered_raw_id]
    assert priority == "none"
    assert source_object.metadata_extra == {"connector_state": {"cursor": "abc"}}
    assert db.flush.await_count == 1
    assert db.commit.await_count == 1


@pytest.mark.asyncio
async def test_commit_and_queue_normalization_queues_claimed_pending_ids():
    tenant_id = uuid4()
    source_object_id = uuid4()
    current_run = SimpleNamespace(id=uuid4(), errors=None, status="completed", completed_at=None)
    recovered_raw_id = uuid4()
    new_raw_id = uuid4()
    db = SimpleNamespace()

    with (
        patch(
            "contextedge.services.sync_worker_service._claim_pending_raw_ids_for_handoff",
            AsyncMock(return_value=([recovered_raw_id, new_raw_id], [recovered_raw_id], "none")),
        ),
        patch("contextedge.services.sync_worker_service.queue_normalize_raw_objects") as queue,
    ):
        await _commit_and_queue_normalization(
            db,
            run=current_run,
            tenant_id=tenant_id,
            source_object_id=source_object_id,
            new_raw_ids=[new_raw_id],
        )

    queue.assert_called_once_with([recovered_raw_id, new_raw_id], tenant_id)


@pytest.mark.asyncio
async def test_commit_and_queue_normalization_persists_only_unqueued_tail_on_handoff_failure():
    tenant_id = uuid4()
    source_object_id = uuid4()
    recovered_raw_id = uuid4()
    pending_raw_id = uuid4()
    current_run = SimpleNamespace(
        id=uuid4(),
        errors={"ingestion": {"raw_objects_created": 1}},
        status="completed",
        completed_at=None,
    )
    db = SimpleNamespace()
    unqueued_raw_id = pending_raw_id

    with (
        patch(
            "contextedge.services.sync_worker_service._claim_pending_raw_ids_for_handoff",
            AsyncMock(return_value=([recovered_raw_id, pending_raw_id], [recovered_raw_id], "none")),
        ),
        patch(
            "contextedge.services.sync_worker_service._reconcile_pending_raw_ids_on_source_object",
            AsyncMock(return_value=[unqueued_raw_id]),
        ) as reconcile,
        patch(
            "contextedge.services.sync_worker_service.queue_normalize_raw_objects",
            side_effect=NormalizeEnqueueError(
                pending_raw_ids=[unqueued_raw_id],
                detail="broker down",
            ),
        ),
        pytest.raises(RuntimeError, match="broker down"),
    ):
        await _commit_and_queue_normalization(
            db,
            run=current_run,
            tenant_id=tenant_id,
            source_object_id=source_object_id,
            new_raw_ids=[pending_raw_id],
        )

    assert current_run.status == "failed"
    assert current_run.errors["ingestion"]["raw_objects_created"] == 1
    assert current_run.errors["handoff"]["pending_raw_ids"] == [str(unqueued_raw_id)]
    assert current_run.errors["handoff"]["pending_raw_count"] == 1
    assert current_run.errors["handoff"]["attempted_raw_count"] == 2
    reconcile.assert_awaited_once()
    kwargs = reconcile.await_args.kwargs
    assert kwargs["tenant_id"] == tenant_id
    assert kwargs["source_object_id"] == source_object_id
    assert kwargs["add_raw_ids"] == [unqueued_raw_id]
    assert kwargs["remove_raw_ids"] == [recovered_raw_id]
    assert isinstance(kwargs["updated_at"], datetime)
