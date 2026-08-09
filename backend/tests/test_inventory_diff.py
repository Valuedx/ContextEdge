"""B3: inventory diffing — silent changes become events.

The governing case: a browser auto-upgrade on an agent machine (the F4
web-driver incident's cause) has no record anywhere; only an
observe-and-diff pass can catch it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from contextedge.services import inventory_diff_service
from contextedge.services.inventory_diff_service import (
    MAX_EVENTS_PER_OBSERVATION,
    SNAPSHOT_KEY,
    diff_states,
    observe_inventory,
)


def test_diff_catches_change_addition_and_removal():
    changes = diff_states(
        {"browser": "118", "agent": "8.4.1", "gone": "x"},
        {"browser": "119", "agent": "8.4.1", "new": "y"},
    )
    assert ("browser", "118", "119") in changes
    assert ("gone", "x", None) in changes
    assert ("new", None, "y") in changes
    assert not any(c[0] == "agent" for c in changes)


def _entity_db(*entities):
    """Resolution now fetches up to two matches (ambiguity check)."""
    db = MagicMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = [e for e in entities if e is not None]
    db.execute = AsyncMock(return_value=result)
    return db


@pytest.mark.asyncio
async def test_first_observation_is_baseline_no_events():
    entity = MagicMock(attributes={}, domain_id=None)
    db = _entity_db(entity)
    with patch.object(inventory_diff_service, "record_state_event", new=AsyncMock()) as rec:
        counts = await observe_inventory(
            db, uuid.uuid4(), ci_name="agent-07", state={"browser": "118"},
        )
    assert counts["baseline"] is True
    assert counts["events"] == 0
    rec.assert_not_awaited()
    assert entity.attributes[SNAPSHOT_KEY] == {"browser": "118"}


@pytest.mark.asyncio
async def test_changed_key_emits_one_event():
    entity = MagicMock(attributes={SNAPSHOT_KEY: {"browser": "118"}}, domain_id=None)
    db = _entity_db(entity)
    with patch.object(
        inventory_diff_service, "record_state_event", new=AsyncMock(return_value=object())
    ) as rec:
        counts = await observe_inventory(
            db, uuid.uuid4(), ci_name="agent-07", state={"browser": "119"},
            observed_at=datetime(2026, 8, 7, tzinfo=UTC),
        )
    assert counts == {"status": "ok", "events": 1, "baseline": False, "changes": 1}
    kwargs = rec.await_args.kwargs
    assert kwargs["event_kind"] == "browser"
    assert kwargs["from_value"] == "118"
    assert kwargs["to_value"] == "119"


@pytest.mark.asyncio
async def test_reshaped_report_is_capped():
    entity = MagicMock(attributes={SNAPSHOT_KEY: {}}, domain_id=None)
    db = _entity_db(entity)
    big_state = {f"key{i}": str(i) for i in range(MAX_EVENTS_PER_OBSERVATION + 30)}
    with patch.object(
        inventory_diff_service, "record_state_event", new=AsyncMock(return_value=object())
    ) as rec:
        counts = await observe_inventory(
            db, uuid.uuid4(), ci_name="agent-07", state=big_state,
        )
    assert counts["events"] == MAX_EVENTS_PER_OBSERVATION
    assert rec.await_count == MAX_EVENTS_PER_OBSERVATION


@pytest.mark.asyncio
async def test_ambiguous_name_refuses_and_writes_nothing():
    """Two same-named CIs: guessing attaches state events to the wrong
    one and contaminates both preceding-change windows."""
    a = MagicMock(attributes={}, domain_id=None)
    b = MagicMock(attributes={}, domain_id=None)
    db = _entity_db(a, b)
    with patch.object(inventory_diff_service, "record_state_event", new=AsyncMock()) as rec:
        counts = await observe_inventory(
            db, uuid.uuid4(), ci_name="agent-07", state={"browser": "119"},
        )
    assert counts["status"] == "ambiguous_ci"
    rec.assert_not_awaited()
    db.add.assert_not_called()
    db.flush.assert_not_awaited()  # nothing persisted, not even a snapshot


@pytest.mark.asyncio
async def test_unknown_ci_is_refused_unless_opted_in():
    db = _entity_db()
    counts = await observe_inventory(
        db, uuid.uuid4(), ci_name="typo-host", state={"browser": "119"},
    )
    assert counts["status"] == "unknown_ci"
    db.add.assert_not_called()

    db2 = _entity_db()
    counts = await observe_inventory(
        db2, uuid.uuid4(), ci_name="new-host", state={"browser": "119"},
        create_missing=True,
    )
    assert counts["status"] == "ok" and counts["baseline"] is True
    created = db2.add.call_args.args[0]
    assert created.entity_type == "configuration_item"


@pytest.mark.asyncio
async def test_external_id_resolution_carries_onto_created_entity():
    db = _entity_db()
    await observe_inventory(
        db, uuid.uuid4(), ci_name="agent-07", state={},
        external_system="automationedge", external_id="ae-1234",
        create_missing=True,
    )
    created = db.add.call_args.args[0]
    assert created.external_system == "automationedge"
    assert created.external_id == "ae-1234"


def test_inventory_endpoint_is_registered():
    import inspect

    from contextedge.api import v1
    from contextedge.api.v1 import inventory

    assert "/report" in {r.path for r in inventory.router.routes}
    # And the v1 aggregate router actually mounted it (an unmounted
    # router passes the line above while serving nothing).
    source = inspect.getsource(v1)
    assert 'include_router(inventory.router, prefix="/inventory"' in source
