"""proposed_depends_on review workflow: agent discoveries become
authored topology only through a human decision, and the proposal
trail survives as closed edges, never deletions."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from contextedge.services import edge_proposal_service as svc


def _proposal(**overrides):
    defaults = dict(
        id=uuid.uuid4(),
        source_node_id=uuid.uuid4(),
        target_node_id=uuid.uuid4(),
        domain_id=None,
        valid_to=None,
        created_at=None,
        metadata_extra={
            "origin": "agent_discovered",
            "rationale": "OrderHub calls the pricing API",
            "evidence_ids": ["e1"],
        },
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _db_returning_proposal(proposal):
    db = MagicMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = proposal
    db.execute = AsyncMock(return_value=result)
    db.flush = AsyncMock()
    return db


@pytest.mark.asyncio
async def test_approve_promotes_to_authored_edge_and_closes_proposal():
    proposal = _proposal()
    db = _db_returning_proposal(proposal)
    authored = SimpleNamespace(id=uuid.uuid4())
    with patch(
        "contextedge.graph.builder.ensure_edge", new=AsyncMock(return_value=authored)
    ) as edge:
        result = await svc.approve_edge_proposal(
            db, uuid.uuid4(), proposal.id, reviewed_by="reviewer-1", note="verified"
        )
    assert result == {"status": "approved", "authored_edge_id": str(authored.id)}
    kwargs = edge.await_args.kwargs
    assert kwargs["edge_type"] == "depends_on"
    assert kwargs["confidence"] == svc.APPROVED_CONFIDENCE
    assert kwargs["metadata"]["origin"] == "agent_proposal_approved"
    assert kwargs["metadata"]["proposal_edge_id"] == str(proposal.id)
    assert kwargs["metadata"]["rationale"] == "OrderHub calls the pricing API"
    assert kwargs["metadata"]["reviewed_by"] == "reviewer-1"
    # Proposal closed with the verdict, never deleted.
    assert proposal.valid_to is not None
    assert proposal.metadata_extra["review"]["decision"] == "approved"
    assert proposal.metadata_extra["review"]["note"] == "verified"


@pytest.mark.asyncio
async def test_reject_closes_without_writing_topology():
    proposal = _proposal()
    db = _db_returning_proposal(proposal)
    with patch("contextedge.graph.builder.ensure_edge", new=AsyncMock()) as edge:
        result = await svc.reject_edge_proposal(
            db, uuid.uuid4(), proposal.id, reviewed_by="reviewer-1", note="not real"
        )
    assert result == {"status": "rejected"}
    edge.assert_not_awaited()
    assert proposal.valid_to is not None
    assert proposal.metadata_extra["review"]["decision"] == "rejected"


@pytest.mark.asyncio
async def test_missing_or_already_reviewed_proposal_errors():
    db = _db_returning_proposal(None)
    assert (
        await svc.approve_edge_proposal(
            db, uuid.uuid4(), uuid.uuid4(), reviewed_by="r"
        )
    )["error"] == "proposal_not_found"
    assert (
        await svc.reject_edge_proposal(db, uuid.uuid4(), uuid.uuid4(), reviewed_by="r")
    )["error"] == "proposal_not_found"


@pytest.mark.asyncio
async def test_list_resolves_ci_names_for_the_reviewer():
    src, dst = uuid.uuid4(), uuid.uuid4()
    proposal = _proposal(source_node_id=src, target_node_id=dst)
    db = MagicMock()
    edges_result = MagicMock()
    edges_result.scalars.return_value.all.return_value = [proposal]
    names_result = MagicMock()
    names_result.all.return_value = [(src, "OrderHub"), (dst, "pricing-api")]
    db.execute = AsyncMock(side_effect=[edges_result, names_result])
    rows = await svc.list_edge_proposals(db, uuid.uuid4())
    assert rows[0]["source_ci"] == "OrderHub"
    assert rows[0]["target_ci"] == "pricing-api"
    assert rows[0]["rationale"] == "OrderHub calls the pricing API"


def test_proposal_routes_are_registered():
    from contextedge.api.v1 import graph

    paths = {r.path for r in graph.router.routes}
    assert "/edge-proposals" in paths
    assert "/edge-proposals/{edge_id}/approve" in paths
    assert "/edge-proposals/{edge_id}/reject" in paths
