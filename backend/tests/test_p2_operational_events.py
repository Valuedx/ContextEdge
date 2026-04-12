from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from contextedge.middleware.request_context import bind_request_context, reset_request_context
from contextedge.services.event_log_service import append_operational_event


@pytest.mark.asyncio
async def test_append_operational_event_uses_request_context_ids():
    tenant_id = uuid4()
    request_id = uuid4()
    correlation_id = uuid4()
    actor_id = uuid4()
    added = []
    db = SimpleNamespace(
        add=lambda obj: added.append(obj),
        flush=AsyncMock(),
    )

    token = bind_request_context(
        request_id=request_id,
        correlation_id=correlation_id,
        causation_id=request_id,
        user_id=actor_id,
    )
    try:
        event = await append_operational_event(
            db,
            tenant_id=tenant_id,
            entity_type="playbook",
            entity_id=uuid4(),
            event_type="playbook.transitioned",
            payload={"to_state": "approved"},
        )
    finally:
        reset_request_context(token)

    assert event.tenant_id == tenant_id
    assert event.correlation_id == correlation_id
    assert event.causation_id == request_id
    assert event.actor_id == actor_id
    assert added[-1] is event
    db.flush.assert_awaited_once()
