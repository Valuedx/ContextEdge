"""B6 fleet grouping: detection thresholds, idempotency/permanence,
reviewer-gated acceptance."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from contextedge.models.case_bridge import EvidenceCaseMembership
from contextedge.models.fleet_group import FleetGroupSuggestion
from contextedge.services.fleet_group_service import (
    accept_fleet_group,
    detect_fleet_groups,
    reject_fleet_group,
)


class _NestedTx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


def _detector_db(edge_rows, existing=None, added=None, events=None):
    added = added if added is not None else []

    async def execute(stmt):
        text = str(stmt)
        result = Mock()
        if "graph_edges" in text:
            result.all.return_value = edge_rows
            return result
        if text.startswith("SELECT fleet_group_suggestions."):
            result.scalar_one_or_none.return_value = existing
            return result
        if text.startswith("SELECT evidence_case_memberships.id"):
            result.scalar_one_or_none.return_value = None
            return result
        result.scalar_one_or_none.return_value = None
        result.all.return_value = []
        return result

    return SimpleNamespace(
        execute=execute,
        add=added.append,
        flush=AsyncMock(),
        begin_nested=Mock(return_value=_NestedTx()),
    ), added


@pytest.mark.asyncio
async def test_three_incidents_same_change_suggest_group():
    """The Windows-patch boot-loop scenario: LPT001/LPT121/DTP055 all
    blame the same change -> one pending suggestion, no memberships."""
    tenant_id = uuid4()
    change = uuid4()
    incidents = [uuid4(), uuid4(), uuid4()]
    edge_rows = [(change, "chg0001", i) for i in incidents]
    db, added = _detector_db(edge_rows)

    counts = await detect_fleet_groups(db, tenant_id)

    assert counts["groups_suggested"] == 1
    (s,) = [a for a in added if isinstance(a, FleetGroupSuggestion)]
    assert s.member_count == 3
    assert s.status == "pending"
    # Detection NEVER attaches members — only reviewer accept does.
    assert not [a for a in added if isinstance(a, EvidenceCaseMembership)]


@pytest.mark.asyncio
async def test_two_incidents_never_suggest():
    """Two same-model failures months apart share no 3-member change
    cluster: nothing is suggested."""
    tenant_id = uuid4()
    change = uuid4()
    db, added = _detector_db([(change, "chg0002", uuid4()), (change, "chg0002", uuid4())])
    counts = await detect_fleet_groups(db, tenant_id)
    assert counts["groups_suggested"] == 0
    assert added == []


@pytest.mark.asyncio
async def test_rejected_group_is_never_resuggested():
    tenant_id = uuid4()
    change = uuid4()
    rejected = FleetGroupSuggestion(
        tenant_id=tenant_id,
        change_ref="chg0003",
        status="rejected",
        member_evidence_ids=[],
    )
    # Re-ingested change: NEW evidence id, same external id — rejection
    # still holds because the key is the external id.
    db, added = _detector_db(
        [(uuid4(), "chg0003", uuid4()) for _ in range(4)], existing=rejected
    )
    counts = await detect_fleet_groups(db, tenant_id)
    assert counts["groups_suggested"] == 0
    assert rejected.status == "rejected"
    assert added == []


@pytest.mark.asyncio
async def test_pending_group_updates_members_on_growth():
    tenant_id = uuid4()
    change = uuid4()
    old_members = [str(uuid4())]
    pending = FleetGroupSuggestion(
        tenant_id=tenant_id,
        change_ref="chg0004",
        status="pending",
        member_evidence_ids=old_members,
        member_count=1,
    )
    incidents = [uuid4(), uuid4(), uuid4(), uuid4()]
    db, added = _detector_db(
        [(change, "chg0004", i) for i in incidents], existing=pending
    )
    counts = await detect_fleet_groups(db, tenant_id)
    assert counts["groups_updated"] == 1
    assert pending.member_count == 4


@pytest.mark.asyncio
async def test_accept_mints_parent_and_attaches_members(monkeypatch):
    tenant_id = uuid4()
    members = [str(uuid4()), str(uuid4()), str(uuid4())]
    suggestion = FleetGroupSuggestion(
        id=uuid4(),
        tenant_id=tenant_id,
        change_ref="chg-1",
        status="pending",
        member_evidence_ids=members,
        member_count=3,
    )
    added = []
    events = []

    async def fake_event(db, **kwargs):
        events.append(kwargs)

    monkeypatch.setattr(
        "contextedge.services.event_log_service.append_operational_event", fake_event
    )

    async def execute(stmt):
        result = Mock()
        result.scalar_one_or_none.return_value = None
        return result

    db = SimpleNamespace(
        execute=execute,
        add=added.append,
        flush=AsyncMock(),
        begin_nested=Mock(return_value=_NestedTx()),
    )

    result = await accept_fleet_group(db, tenant_id, suggestion, "reviewer@acme.com")

    assert result["members_attached"] == 3
    assert suggestion.status == "accepted"
    assert suggestion.parent_case_id is not None
    rows = [a for a in added if isinstance(a, EvidenceCaseMembership)]
    assert len(rows) == 3
    assert all(r.relationship_type == "fleet_member" for r in rows)
    assert len({r.canonical_case_id for r in rows}) == 1  # one parent case
    assert events[0]["event_type"] == "correlation.fleet_group_accepted"


@pytest.mark.asyncio
async def test_reject_is_permanent_and_attaches_nothing():
    tenant_id = uuid4()
    suggestion = FleetGroupSuggestion(
        id=uuid4(),
        tenant_id=tenant_id,
        change_ref="chg-2",
        status="pending",
        member_evidence_ids=[str(uuid4())],
    )
    added = []
    db = SimpleNamespace(add=added.append, flush=AsyncMock())

    await reject_fleet_group(db, tenant_id, suggestion, "reviewer@acme.com")

    assert suggestion.status == "rejected"
    assert suggestion.reviewed_at is not None
    assert added == []
