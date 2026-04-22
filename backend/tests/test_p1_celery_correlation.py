"""Tests for Celery correlation-ID propagation (W5-6.2).

The end-to-end chain we're exercising:

    HTTP request (middleware sets ContextVar)
        ↓
    task.delay(...)         ← before_task_publish injects headers
        ↓  (broker)
    worker executes         ← task_prerun rebinds ContextVar
        ↓
    service code reads      current_correlation_id()
        ↓
    task returns            ← task_postrun resets ContextVar

We test each signal-handler in isolation using the raw Celery signal
plumbing — we don't need a live broker to validate that the header
in/out contract works.
"""

import uuid
from types import SimpleNamespace

import pytest

from contextedge.middleware.request_context import (
    bind_request_context,
    current_correlation_id,
    current_request_id,
    reset_request_context,
)
from contextedge.workers.celery_app import (
    _bind_worker_context,
    _inject_correlation_headers,
    _release_worker_context,
)


def test_before_publish_injects_ids_into_headers():
    request_id = uuid.uuid4()
    correlation_id = uuid.uuid4()
    causation_id = uuid.uuid4()
    token = bind_request_context(
        request_id=request_id,
        correlation_id=correlation_id,
        causation_id=causation_id,
    )
    try:
        headers: dict = {}
        _inject_correlation_headers(headers=headers)

        assert headers["request_id"] == str(request_id)
        assert headers["correlation_id"] == str(correlation_id)
        assert headers["causation_id"] == str(causation_id)
    finally:
        reset_request_context(token)


def test_before_publish_is_noop_when_no_context():
    """Tasks enqueued from Celery Beat / tests have no HTTP request
    context — the signal must not crash and must not add bogus ids."""
    headers: dict = {}
    _inject_correlation_headers(headers=headers)
    assert headers == {}


def test_before_publish_does_not_clobber_existing_headers():
    """If a caller manually sets a header (e.g. forwarded from an upstream
    trace), the signal must leave it untouched."""
    custom = str(uuid.uuid4())
    token = bind_request_context(correlation_id=uuid.uuid4())
    try:
        headers = {"correlation_id": custom}
        _inject_correlation_headers(headers=headers)
        assert headers["correlation_id"] == custom
    finally:
        reset_request_context(token)


def test_before_publish_handles_none_headers_without_error():
    _inject_correlation_headers(headers=None)  # should not raise


def test_worker_prerun_binds_ids_from_headers():
    """Simulate a worker starting a task with correlation headers —
    the prerun signal should bind them into the ContextVar so any
    service code running inside the task sees the same IDs."""
    task_id = str(uuid.uuid4())
    correlation_id = uuid.uuid4()
    request_id = uuid.uuid4()

    task = SimpleNamespace(
        request=SimpleNamespace(
            headers={
                "correlation_id": str(correlation_id),
                "request_id": str(request_id),
            }
        )
    )

    _bind_worker_context(task_id=task_id, task=task)
    try:
        assert current_correlation_id() == correlation_id
        assert current_request_id() == request_id
    finally:
        _release_worker_context(task_id=task_id)

    # Post-release, ContextVar should be back to the outer-scope value
    # (None in this test).
    assert current_correlation_id() is None


def test_worker_prerun_ignores_malformed_headers():
    """If a header value isn't a UUID, skip it rather than crashing the
    task before it even starts."""
    task_id = str(uuid.uuid4())
    task = SimpleNamespace(
        request=SimpleNamespace(
            headers={"correlation_id": "not-a-uuid", "request_id": ""}
        )
    )

    _bind_worker_context(task_id=task_id, task=task)
    try:
        assert current_correlation_id() is None
    finally:
        _release_worker_context(task_id=task_id)


def test_worker_prerun_is_noop_when_no_task_object():
    _bind_worker_context(task_id=None, task=None)  # must not raise


def test_worker_postrun_tolerates_unknown_task_id():
    """Celery sometimes fires postrun for tasks that never got a prerun
    bind (e.g. import-time failures). The handler must tolerate it."""
    _release_worker_context(task_id="never-seen")  # no KeyError


def test_concurrent_tasks_each_keep_their_own_token():
    """Two tasks overlapping in the same process must not stomp on each
    other's ContextVar tokens."""
    task_a = str(uuid.uuid4())
    task_b = str(uuid.uuid4())
    corr_a = uuid.uuid4()
    corr_b = uuid.uuid4()

    task_a_obj = SimpleNamespace(
        request=SimpleNamespace(headers={"correlation_id": str(corr_a)})
    )
    task_b_obj = SimpleNamespace(
        request=SimpleNamespace(headers={"correlation_id": str(corr_b)})
    )

    _bind_worker_context(task_id=task_a, task=task_a_obj)
    # Task B starts while A is still running — B's bind wins the ContextVar.
    _bind_worker_context(task_id=task_b, task=task_b_obj)
    try:
        assert current_correlation_id() == corr_b
    finally:
        _release_worker_context(task_id=task_b)
        _release_worker_context(task_id=task_a)


@pytest.mark.asyncio
async def test_append_operational_event_picks_up_worker_correlation_id():
    """Integration-ish: a task running inside ``_bind_worker_context``
    should produce operational_events tagged with the inherited
    correlation_id without any caller code needing to pass it
    explicitly."""
    from contextedge.services.event_log_service import append_operational_event

    task_id = str(uuid.uuid4())
    correlation_id = uuid.uuid4()
    tenant_id = uuid.uuid4()

    task = SimpleNamespace(
        request=SimpleNamespace(headers={"correlation_id": str(correlation_id)})
    )

    captured: list = []

    class _Db:
        def add(self, obj):
            captured.append(obj)

        async def flush(self):
            pass

    _bind_worker_context(task_id=task_id, task=task)
    try:
        await append_operational_event(
            _Db(),
            tenant_id=tenant_id,
            entity_type="test",
            event_type="test.fired",
        )
    finally:
        _release_worker_context(task_id=task_id)

    assert len(captured) == 1
    assert captured[0].correlation_id == correlation_id
