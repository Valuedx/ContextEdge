"""B2/B4: state-transition events and the preceding-change seed layer."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from contextedge.services import event_evidence_service


def _db_returning(existing_id=None, entity=None):
    db = MagicMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    first = MagicMock()
    first.scalar_one_or_none.return_value = existing_id
    second = MagicMock()
    second.scalar_one_or_none.return_value = entity
    db.execute = AsyncMock(side_effect=[first, second])
    return db


@pytest.mark.asyncio
async def test_event_is_born_classified_and_summarized():
    """No LLM ever runs on an event: it must arrive already operational,
    already summarized, with its timestamp on evidence_time."""
    db = _db_returning(existing_id=None, entity=MagicMock(id=uuid.uuid4()))
    occurred = datetime(2026, 8, 1, 10, 0, tzinfo=UTC)
    with patch("contextedge.graph.builder.ensure_edge", new=AsyncMock()) as edge:
        ev = await event_evidence_service.record_state_event(
            db,
            uuid.uuid4(),
            ci_name="agent-prod-07",
            event_kind="browser_version",
            from_value="118",
            to_value="119",
            occurred_at=occurred,
        )
    assert ev is not None
    assert ev.evidence_type == "event"
    assert ev.relevance_state == "operational"
    assert ev.body_summary == ev.title
    assert ev.evidence_time == occurred
    assert "118 -> 119" in ev.title
    edge.assert_awaited_once()


@pytest.mark.asyncio
async def test_duplicate_observation_is_not_recorded_twice():
    db = _db_returning(existing_id=uuid.uuid4())
    out = await event_evidence_service.record_state_event(
        db,
        uuid.uuid4(),
        ci_name="agent-prod-07",
        event_kind="browser_version",
        from_value="118",
        to_value="119",
        occurred_at=datetime(2026, 8, 1, 10, 0, tzinfo=UTC),
    )
    assert out is None
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_ci_link_failure_never_drops_the_event():
    db = _db_returning(existing_id=None, entity=None)
    # Entity find-or-create path: second execute returns None, then the
    # flush for the new entity blows up — the event must survive.
    db.flush = AsyncMock(side_effect=[None, RuntimeError("entity flush failed")])
    ev = await event_evidence_service.record_state_event(
        db,
        uuid.uuid4(),
        ci_name="agent-prod-07",
        event_kind="config_hash",
        from_value=None,
        to_value="abc123",
        occurred_at=datetime(2026, 8, 1, tzinfo=UTC),
    )
    assert ev is not None


def test_preceding_seed_layer_is_registered():
    """The B4 layer runs inside resolve_seeds and anchors only on
    incident-shaped evidence types."""
    from contextedge.graph.agent.repository import SQLAlchemyAgentGraphRepository as Repo

    assert hasattr(Repo, "_seed_preceding_changes")
    assert "change" not in Repo._INCIDENT_EVIDENCE_TYPES
    assert "event" not in Repo._INCIDENT_EVIDENCE_TYPES
    assert "ticket" in Repo._INCIDENT_EVIDENCE_TYPES
    assert Repo.PRECEDING_WINDOW_DAYS == 7
