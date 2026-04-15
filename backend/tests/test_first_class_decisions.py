"""Tests for first-class decision trace models and service.

Covers: Decision creation with options/graph edges, outcome recording,
decision chains, similar-decision queries, backward-compat trace event,
and decision effectiveness aggregation.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch, call
from uuid import uuid4

import pytest

from contextedge.graph.builder import (
    ensure_edge,
    link_decision_evidence,
    link_decision_option,
    link_decision_outcome,
    link_decision_chain,
    link_decision_policy,
)
from contextedge.models.decision import Decision, DecisionOption, DecisionOutcome
from contextedge.models.pattern import GraphEdge


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _ScalarOneOrNoneResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _ScalarsResult:
    def __init__(self, values):
        self._values = values

    def all(self):
        return self._values


class _ScalarsProxy:
    def __init__(self, values):
        self._values = values

    def scalars(self):
        return _ScalarsResult(self._values)


def _make_db(side_effects=None):
    added: list = []
    execute_kwargs = {}
    if side_effects is not None:
        execute_kwargs["side_effect"] = side_effects
    db = SimpleNamespace(
        execute=AsyncMock(**execute_kwargs),
        add=lambda obj: added.append(obj),
        flush=AsyncMock(),
        get=AsyncMock(return_value=None),
        refresh=AsyncMock(),
    )
    return db, added


# =========================================================================
# Graph edge helpers for decisions
# =========================================================================

@pytest.mark.asyncio
async def test_link_decision_evidence_edge():
    """link_decision_evidence creates decision -> evidence 'based_on' edge."""
    db, added = _make_db(side_effects=[_ScalarOneOrNoneResult(None)])
    tenant_id = uuid4()
    decision_id = uuid4()
    evidence_id = uuid4()

    edge = await link_decision_evidence(db, tenant_id, decision_id, evidence_id)

    assert isinstance(edge, GraphEdge)
    assert edge.source_node_type == "decision"
    assert edge.source_node_id == decision_id
    assert edge.target_node_type == "evidence"
    assert edge.target_node_id == evidence_id
    assert edge.edge_type == "based_on"


@pytest.mark.asyncio
async def test_link_decision_option_creates_considered_edge():
    """link_decision_option creates 'considered' edge for non-selected option."""
    db, added = _make_db(side_effects=[_ScalarOneOrNoneResult(None)])
    tenant_id = uuid4()
    decision_id = uuid4()
    option_id = uuid4()

    edges = await link_decision_option(db, tenant_id, decision_id, option_id, selected=False)

    assert len(edges) == 1
    assert edges[0].edge_type == "considered"
    assert edges[0].source_node_type == "decision"
    assert edges[0].target_node_type == "decision_option"


@pytest.mark.asyncio
async def test_link_decision_option_creates_considered_and_chose_edges():
    """link_decision_option creates both 'considered' and 'chose' for selected option."""
    db, added = _make_db(side_effects=[
        _ScalarOneOrNoneResult(None),
        _ScalarOneOrNoneResult(None),
    ])
    tenant_id = uuid4()
    decision_id = uuid4()
    option_id = uuid4()

    edges = await link_decision_option(db, tenant_id, decision_id, option_id, selected=True)

    assert len(edges) == 2
    edge_types = {e.edge_type for e in edges}
    assert "considered" in edge_types
    assert "chose" in edge_types


@pytest.mark.asyncio
async def test_link_decision_outcome_edge():
    """link_decision_outcome creates 'resulted_in' edge."""
    db, added = _make_db(side_effects=[_ScalarOneOrNoneResult(None)])
    tenant_id = uuid4()
    decision_id = uuid4()
    outcome_id = uuid4()

    edge = await link_decision_outcome(db, tenant_id, decision_id, outcome_id)

    assert edge.edge_type == "resulted_in"
    assert edge.source_node_type == "decision"
    assert edge.target_node_type == "decision_outcome"


@pytest.mark.asyncio
async def test_link_decision_chain_edge():
    """link_decision_chain creates 'followed_by' edge."""
    db, added = _make_db(side_effects=[_ScalarOneOrNoneResult(None)])
    tenant_id = uuid4()
    parent_id = uuid4()
    child_id = uuid4()

    edge = await link_decision_chain(db, tenant_id, parent_id, child_id)

    assert edge.edge_type == "followed_by"
    assert edge.source_node_type == "decision"
    assert edge.source_node_id == parent_id
    assert edge.target_node_type == "decision"
    assert edge.target_node_id == child_id


@pytest.mark.asyncio
async def test_link_decision_policy_edge():
    """link_decision_policy creates 'applied_policy' edge."""
    db, added = _make_db(side_effects=[_ScalarOneOrNoneResult(None)])
    tenant_id = uuid4()
    decision_id = uuid4()
    policy_id = uuid4()

    edge = await link_decision_policy(db, tenant_id, decision_id, policy_id)

    assert edge.edge_type == "applied_policy"
    assert edge.target_node_type == "tenant_policy"
    assert edge.target_node_id == policy_id


# =========================================================================
# Decision model instantiation
# =========================================================================

def test_decision_model_explicit_values():
    """Decision model stores explicit field values."""
    d = Decision(
        tenant_id=uuid4(),
        decision_type="classify_issue",
        agent_step="diagnostics",
        rationale_summary="Test rationale",
        actor_type="ai",
        status="pending",
        approval_required=False,
        human_override=False,
    )
    assert d.actor_type == "ai"
    assert d.status == "pending"
    assert d.approval_required is False
    assert d.human_override is False
    assert d.decision_type == "classify_issue"


def test_decision_option_model():
    """DecisionOption model stores field values."""
    opt = DecisionOption(
        decision_id=uuid4(),
        tenant_id=uuid4(),
        action="restart_workflow",
        selected=False,
    )
    assert opt.selected is False
    assert opt.suitability is None
    assert opt.risk_level is None


def test_decision_outcome_model():
    """DecisionOutcome model stores execution result."""
    oc = DecisionOutcome(
        decision_id=uuid4(),
        tenant_id=uuid4(),
        action_executed="restart_workflow",
        execution_result="failure",
        follow_up_needed=False,
    )
    assert oc.execution_result == "failure"
    assert oc.follow_up_needed is False


# =========================================================================
# Service: create_decision
# =========================================================================

@pytest.mark.asyncio
@patch("contextedge.services.decision_trace_service.append_operational_event", new_callable=AsyncMock)
@patch("contextedge.services.decision_trace_service.append_trace_event", new_callable=AsyncMock)
@patch("contextedge.services.decision_trace_service.link_decision_option", new_callable=AsyncMock, return_value=[])
async def test_create_decision_basic(mock_link_opt, mock_trace, mock_op_event):
    """create_decision creates Decision + Options + edges + operational event."""
    from contextedge.services import decision_trace_service
    from contextedge.services.decision_trace_service import create_decision

    mock_ev_linker = AsyncMock()
    original_linkers = decision_trace_service._REF_TYPE_TO_LINKER.copy()
    decision_trace_service._REF_TYPE_TO_LINKER["evidence"] = mock_ev_linker

    try:
        db, added = _make_db()
        db.flush = AsyncMock()
        db.refresh = AsyncMock()

        tenant_id = uuid4()
        session_id = uuid4()
        evidence_id = uuid4()

        result = await create_decision(
            db,
            tenant_id=tenant_id,
            decision_type="classify_issue",
            agent_step="diagnostics",
            rationale_summary="Failure matched network issue pattern",
            session_id=session_id,
            confidence=0.87,
            compact_trace="Network share issue detected",
            evidence_refs=[{"ref_type": "evidence", "ref_id": str(evidence_id), "description": "ticket"}],
            options=[
                {"action": "restart_workflow", "suitability": 0.3, "risk_level": "low", "selected": False, "rejection_reason": "failed in prior cases"},
                {"action": "verify_output_path", "suitability": 0.87, "risk_level": "low", "selected": True},
            ],
        )

        assert isinstance(result, Decision)
        assert result.decision_type == "classify_issue"
        assert result.confidence == 0.87

        decision_objs = [o for o in added if isinstance(o, Decision)]
        option_objs = [o for o in added if isinstance(o, DecisionOption)]
        assert len(decision_objs) == 1
        assert len(option_objs) == 2

        mock_ev_linker.assert_awaited_once()
        assert mock_link_opt.await_count == 2

        mock_trace.assert_awaited_once()
        mock_op_event.assert_awaited_once()
        op_payload = mock_op_event.call_args.kwargs.get("payload", {})
        assert op_payload["decision_type"] == "classify_issue"
        assert op_payload["options_count"] == 2
    finally:
        decision_trace_service._REF_TYPE_TO_LINKER.update(original_linkers)


@pytest.mark.asyncio
@patch("contextedge.services.decision_trace_service.append_operational_event", new_callable=AsyncMock)
@patch("contextedge.services.decision_trace_service.append_trace_event", new_callable=AsyncMock)
async def test_create_decision_no_session_skips_trace(mock_trace, mock_op_event):
    """When session_id is None, no compact trace event is appended."""
    from contextedge.services.decision_trace_service import create_decision

    db, added = _make_db()
    db.flush = AsyncMock()
    db.refresh = AsyncMock()

    await create_decision(
        db,
        tenant_id=uuid4(),
        decision_type="classify_issue",
        agent_step="diagnostics",
        rationale_summary="Test",
    )

    mock_trace.assert_not_awaited()
    mock_op_event.assert_awaited_once()


@pytest.mark.asyncio
@patch("contextedge.services.decision_trace_service.append_operational_event", new_callable=AsyncMock)
@patch("contextedge.services.decision_trace_service.append_trace_event", new_callable=AsyncMock)
@patch("contextedge.services.decision_trace_service.link_decision_chain", new_callable=AsyncMock)
async def test_create_decision_with_parent_creates_chain_edge(mock_chain, mock_trace, mock_op_event):
    """When parent_decision_id is set, a followed_by edge is created."""
    from contextedge.services.decision_trace_service import create_decision

    db, added = _make_db()
    db.flush = AsyncMock()
    db.refresh = AsyncMock()

    parent_id = uuid4()
    await create_decision(
        db,
        tenant_id=uuid4(),
        decision_type="escalate_to_human",
        agent_step="remediation",
        rationale_summary="Escalation needed",
        parent_decision_id=parent_id,
    )

    mock_chain.assert_awaited_once()
    chain_args = mock_chain.call_args
    assert chain_args[0][2] == parent_id


# =========================================================================
# Service: record_outcome
# =========================================================================

@pytest.mark.asyncio
@patch("contextedge.services.decision_trace_service.append_operational_event", new_callable=AsyncMock)
@patch("contextedge.services.decision_trace_service.link_decision_outcome", new_callable=AsyncMock)
@patch("contextedge.services.decision_trace_service.get_decision")
async def test_record_outcome_creates_outcome_and_edge(mock_get, mock_link_out, mock_op_event):
    """record_outcome creates DecisionOutcome row, resulted_in edge, and marks decision completed."""
    from contextedge.services.decision_trace_service import record_outcome

    tenant_id = uuid4()
    decision_id = uuid4()
    decision = Decision(
        id=decision_id,
        tenant_id=tenant_id,
        decision_type="restart_workflow",
        agent_step="remediation",
        rationale_summary="Test",
        status="pending",
    )
    mock_get.return_value = decision

    db, added = _make_db()
    db.flush = AsyncMock()
    db.refresh = AsyncMock()

    outcome = await record_outcome(
        db,
        tenant_id=tenant_id,
        decision_id=decision_id,
        action_executed="restart_workflow",
        execution_result="failure",
        result_details={"error": "timeout"},
    )

    assert isinstance(outcome, DecisionOutcome)
    assert outcome.execution_result == "failure"
    assert decision.status == "completed"

    mock_link_out.assert_awaited_once()
    mock_op_event.assert_awaited_once()


@pytest.mark.asyncio
@patch("contextedge.services.decision_trace_service.append_operational_event", new_callable=AsyncMock)
@patch("contextedge.services.decision_trace_service.link_decision_outcome", new_callable=AsyncMock)
@patch("contextedge.services.decision_trace_service.link_decision_chain", new_callable=AsyncMock)
@patch("contextedge.services.decision_trace_service.get_decision")
async def test_record_outcome_with_follow_up_creates_chain(mock_get, mock_chain, mock_link_out, mock_op_event):
    """record_outcome with follow_up_decision_id creates a followed_by edge."""
    from contextedge.services.decision_trace_service import record_outcome

    tenant_id = uuid4()
    decision_id = uuid4()
    follow_up_id = uuid4()
    decision = Decision(
        id=decision_id,
        tenant_id=tenant_id,
        decision_type="verify_dependency",
        agent_step="diagnostics",
        rationale_summary="Test",
        status="pending",
    )
    mock_get.return_value = decision

    db, added = _make_db()
    db.flush = AsyncMock()
    db.refresh = AsyncMock()

    await record_outcome(
        db,
        tenant_id=tenant_id,
        decision_id=decision_id,
        action_executed="verify_path",
        execution_result="success",
        follow_up_needed=True,
        follow_up_decision_id=follow_up_id,
    )

    mock_chain.assert_awaited_once()
    chain_args = mock_chain.call_args
    assert chain_args[0][2] == decision_id
    assert chain_args[0][3] == follow_up_id


@pytest.mark.asyncio
@patch("contextedge.services.decision_trace_service.get_decision", return_value=None)
async def test_record_outcome_returns_none_for_missing_decision(mock_get):
    """record_outcome returns None when the decision does not exist."""
    from contextedge.services.decision_trace_service import record_outcome

    db, _ = _make_db()
    result = await record_outcome(
        db,
        tenant_id=uuid4(),
        decision_id=uuid4(),
        action_executed="restart",
        execution_result="failure",
    )
    assert result is None


# =========================================================================
# Service: get_decision_chain
# =========================================================================

@pytest.mark.asyncio
async def test_get_decision_chain_single_node():
    """get_decision_chain returns just the root when no parent/children."""
    from contextedge.services.decision_trace_service import get_decision_chain

    tenant_id = uuid4()
    decision_id = uuid4()
    decision = Decision(
        id=decision_id,
        tenant_id=tenant_id,
        decision_type="classify_issue",
        agent_step="diagnostics",
        rationale_summary="Test",
        parent_decision_id=None,
    )

    with patch(
        "contextedge.services.decision_trace_service.get_decision",
        new_callable=AsyncMock,
        return_value=decision,
    ):
        db, _ = _make_db(side_effects=[_ScalarsProxy([])])
        chain = await get_decision_chain(db, tenant_id=tenant_id, decision_id=decision_id)

    assert len(chain) == 1
    assert chain[0].id == decision_id


# =========================================================================
# Service: find_similar_decisions
# =========================================================================

@pytest.mark.asyncio
async def test_find_similar_decisions_filters_by_type():
    """find_similar_decisions returns matching decisions by type."""
    from contextedge.services.decision_trace_service import find_similar_decisions

    tenant_id = uuid4()
    d1 = Decision(
        id=uuid4(), tenant_id=tenant_id,
        decision_type="restart_workflow", agent_step="remediation",
        rationale_summary="Test",
    )

    db, _ = _make_db(side_effects=[_ScalarsProxy([d1])])
    results = await find_similar_decisions(
        db, tenant_id=tenant_id, decision_type="restart_workflow",
    )

    assert len(results) == 1
    assert results[0].decision_type == "restart_workflow"


# =========================================================================
# Pydantic schemas
# =========================================================================

def test_decision_response_schema():
    """DecisionResponse round-trips from ORM-like dict."""
    from contextedge.schemas.decision import DecisionResponse
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    data = {
        "id": uuid4(),
        "tenant_id": uuid4(),
        "domain_id": None,
        "session_id": None,
        "parent_decision_id": None,
        "decision_type": "classify_issue",
        "agent_step": "diagnostics",
        "actor_type": "ai",
        "actor_id": None,
        "context_snapshot": {"workflow": "MG22"},
        "evidence_summary": [{"ref_type": "evidence", "ref_id": "abc", "description": "ticket"}],
        "rationale_summary": "Network issue pattern",
        "confidence": 0.87,
        "uncertainty_notes": None,
        "compact_trace": "Network share issue",
        "explanation": None,
        "approval_required": False,
        "policy_refs": [],
        "human_override": False,
        "status": "pending",
        "created_at": now,
        "updated_at": now,
        "options": [],
        "outcomes": [],
    }
    resp = DecisionResponse.model_validate(data)
    assert resp.decision_type == "classify_issue"
    assert resp.confidence == 0.87


def test_decision_create_schema():
    """DecisionCreate validates a create payload."""
    from contextedge.schemas.decision import DecisionCreate

    payload = DecisionCreate(
        decision_type="restart_workflow",
        agent_step="remediation",
        rationale_summary="Restart is the fallback",
        confidence=0.4,
        options=[
            {"action": "restart_workflow", "suitability": 0.4, "selected": True},
        ],
        evidence_refs=[
            {"ref_type": "evidence", "ref_id": str(uuid4()), "description": "log entry"},
        ],
    )
    assert payload.decision_type == "restart_workflow"
    assert len(payload.options) == 1
    assert payload.options[0].selected is True


def test_decision_outcome_create_schema():
    """DecisionOutcomeCreate validates an outcome payload."""
    from contextedge.schemas.decision import DecisionOutcomeCreate

    payload = DecisionOutcomeCreate(
        action_executed="restart_workflow",
        execution_result="failure",
        result_details={"error": "timeout"},
        follow_up_needed=True,
    )
    assert payload.execution_result == "failure"
    assert payload.follow_up_needed is True
