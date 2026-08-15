"""Tests for decision capture in the context graph.

Tier 2: governed decision edges from execution_service
Tier 1: AI decision extraction and graph linking from evidence
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import pytest

from contextedge.graph.builder import ensure_edge
from contextedge.models.pattern import GraphEdge


# ---------------------------------------------------------------------------
# Helpers (shared with test_graph_builder.py pattern)
# ---------------------------------------------------------------------------

class _ScalarOneOrNoneResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def scalar_one(self):
        # Count-style queries (e.g. complete_execution's open-step check)
        # expect a number; a None stub means "nothing open".
        return self._value if self._value is not None else 0


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
    """SELECT results come from *side_effects* in order; ensure_edge's
    ON CONFLICT INSERT ... RETURNING is echoed back as a GraphEdge built
    from the statement's bound values (mirrors test_graph_builder)."""
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.dialects.postgresql.dml import Insert as _PgInsert

    from contextedge.models.pattern import GraphEdge as _GraphEdge

    added: list = []
    select_results = list(side_effects or [])

    async def _execute(stmt):
        if isinstance(stmt, _PgInsert):
            params = stmt.compile(dialect=postgresql.dialect()).params
            return _ScalarOneOrNoneResult(_GraphEdge(**dict(params)))
        if not select_results:
            # start_execution now runs bookkeeping SELECTs of its own — the
            # F10 trust-suspension scan, the F8 duplicate lookup and attempt
            # count. A test that stated no result for them wants "nothing
            # found", not an IndexError.
            empty = Mock()
            empty.scalars.return_value.all.return_value = []
            empty.scalar_one.return_value = 0
            empty.scalar_one_or_none.return_value = None
            empty.all.return_value = []
            empty.first.return_value = None
            return empty
        return select_results.pop(0)

    db = SimpleNamespace(
        execute=AsyncMock(side_effect=_execute),
        add=lambda obj: added.append(obj),
        flush=AsyncMock(),
        get=AsyncMock(return_value=None),
        refresh=AsyncMock(),
    )
    return db, added


# =========================================================================
# Tier 2: governed decision edges (ensure_edge with new edge types)
# =========================================================================

@pytest.mark.asyncio
async def test_executed_playbook_edge():
    """start_execution creates session -> playbook edge via ensure_edge."""
    db, added = _make_db(side_effects=[_ScalarOneOrNoneResult(None)])
    tenant_id = uuid4()
    session_id = uuid4()
    playbook_id = uuid4()
    run_id = uuid4()

    edge = await ensure_edge(
        db, tenant_id,
        "session", session_id,
        "playbook", playbook_id,
        "executed_playbook",
        metadata={
            "execution_run_id": str(run_id),
            "automation_mode": "human_confirmed",
        },
    )

    assert isinstance(edge, GraphEdge)
    assert edge.source_node_type == "session"
    assert edge.source_node_id == session_id
    assert edge.target_node_type == "playbook"
    assert edge.target_node_id == playbook_id
    assert edge.edge_type == "executed_playbook"
    assert edge.metadata_extra["automation_mode"] == "human_confirmed"
    # Miss path inserts via ON CONFLICT DO NOTHING, not session.add().
    assert added == []


@pytest.mark.asyncio
async def test_approved_by_edge():
    """decide_approval creates approval_request -> user edge for approvals."""
    db, added = _make_db(side_effects=[_ScalarOneOrNoneResult(None)])
    tenant_id = uuid4()
    approval_id = uuid4()
    user_id = uuid4()

    edge = await ensure_edge(
        db, tenant_id,
        "approval_request", approval_id,
        "user", user_id,
        "approved_by",
        metadata={"comment": "Looks good", "safety_class": "low_side_effect"},
    )

    assert edge.edge_type == "approved_by"
    assert edge.source_node_type == "approval_request"
    assert edge.target_node_type == "user"
    assert edge.target_node_id == user_id
    assert edge.metadata_extra["comment"] == "Looks good"


@pytest.mark.asyncio
async def test_denied_by_edge():
    """decide_approval creates denial edge."""
    db, added = _make_db(side_effects=[_ScalarOneOrNoneResult(None)])
    tenant_id = uuid4()

    edge = await ensure_edge(
        db, tenant_id,
        "approval_request", uuid4(),
        "user", uuid4(),
        "denied_by",
        metadata={"comment": "Too risky", "safety_class": "destructive"},
    )

    assert edge.edge_type == "denied_by"
    assert edge.metadata_extra["safety_class"] == "destructive"


@pytest.mark.asyncio
async def test_execution_outcome_edge():
    """complete_execution creates execution_run -> playbook edge."""
    db, added = _make_db(side_effects=[_ScalarOneOrNoneResult(None)])
    tenant_id = uuid4()
    run_id = uuid4()
    playbook_id = uuid4()

    edge = await ensure_edge(
        db, tenant_id,
        "execution_run", run_id,
        "playbook", playbook_id,
        "execution_outcome",
        metadata={"outcome": "success", "outcome_summary": "VPN restored"},
    )

    assert edge.edge_type == "execution_outcome"
    assert edge.source_node_type == "execution_run"
    assert edge.target_node_type == "playbook"
    assert edge.metadata_extra["outcome"] == "success"


# =========================================================================
# Tier 1: decision extractor
# =========================================================================

@pytest.mark.asyncio
async def test_extract_decisions_returns_list():
    from contextedge.ai.extractors.decision_extractor import extract_decisions

    mock_response = {
        "decisions": [
            {
                "decision_type": "restart",
                "actor": "jsmith",
                "target": "vpn-gw-east-01",
                "action": "Restarted the VPN gateway",
                "context": "After cert renewal",
            }
        ]
    }
    with patch(
        "contextedge.ai.extractors.decision_extractor.llm_complete_json",
        new_callable=AsyncMock,
        return_value=mock_response,
    ):
        result = await extract_decisions("Engineer jsmith restarted vpn-gw-east-01")

    assert len(result) == 1
    assert result[0]["decision_type"] == "restart"
    assert result[0]["actor"] == "jsmith"
    assert result[0]["target"] == "vpn-gw-east-01"


@pytest.mark.asyncio
async def test_extract_decisions_empty_content():
    from contextedge.ai.extractors.decision_extractor import extract_decisions

    result = await extract_decisions("")
    assert result == []

    result = await extract_decisions("short")
    assert result == []


@pytest.mark.asyncio
async def test_extract_decisions_no_decisions_found():
    from contextedge.ai.extractors.decision_extractor import extract_decisions

    with patch(
        "contextedge.ai.extractors.decision_extractor.llm_complete_json",
        new_callable=AsyncMock,
        return_value={"decisions": []},
    ):
        result = await extract_decisions("This is just a status update with no actions.")

    assert result == []


# =========================================================================
# Tier 1: decision service (link_evidence_decisions)
# =========================================================================

@pytest.mark.asyncio
async def test_link_evidence_decisions_creates_edges():
    from contextedge.services.decision_service import link_evidence_decisions

    tenant_id = uuid4()
    evidence_id = uuid4()
    actor_canonical_id = uuid4()
    target_canonical_id = uuid4()

    evidence = SimpleNamespace(
        id=evidence_id,
        domain_id=None,
        canonical_entity_refs={},
    )

    mock_decisions = [
        {
            "decision_type": "restart",
            "actor": "jsmith",
            "target": "vpn-gw-east-01",
            "action": "Restarted the VPN gateway",
            "context": "After cert renewal",
        }
    ]

    actor_resolved = [{"canonical_id": actor_canonical_id, "canonical_name": "John Smith", "entity_type": "person", "matched_via": "alias", "alias": "jsmith", "confidence": 0.9}]
    target_resolved = [{"canonical_id": target_canonical_id, "canonical_name": "VPN Gateway East 01", "entity_type": "service", "matched_via": "new", "alias": "vpn-gw-east-01", "confidence": 0.8}]

    ensure_edge_calls = []
    original_ensure_edge_side_effects = [
        _ScalarOneOrNoneResult(None),
        _ScalarOneOrNoneResult(None),
    ]
    call_idx = [0]

    async def mock_ensure_edge(db, tid, src_type, src_id, tgt_type, tgt_id, etype, **kwargs):
        ensure_edge_calls.append((src_type, src_id, tgt_type, tgt_id, etype))
        edge = GraphEdge(
            tenant_id=tid,
            source_node_type=src_type,
            source_node_id=src_id,
            target_node_type=tgt_type,
            target_node_id=tgt_id,
            edge_type=etype,
            metadata_extra=kwargs.get("metadata"),
        )
        return edge

    resolve_call_count = [0]

    async def mock_resolve(db, tid, entities, source_id=None):
        result = actor_resolved if resolve_call_count[0] == 0 else target_resolved
        resolve_call_count[0] += 1
        return result

    db, _ = _make_db()

    with (
        patch("contextedge.services.decision_service.extract_decisions", new_callable=AsyncMock, return_value=mock_decisions),
        patch("contextedge.services.decision_service.ensure_edge", side_effect=mock_ensure_edge),
        patch("contextedge.services.decision_service.resolve_extracted_entities", side_effect=mock_resolve),
        patch("contextedge.services.decision_service.append_operational_event", new_callable=AsyncMock),
    ):
        result = await link_evidence_decisions(
            db,
            tenant_id=tenant_id,
            evidence=evidence,
            content="Engineer jsmith restarted vpn-gw-east-01 after cert renewal",
            source_id=uuid4(),
        )

    assert len(result) == 1
    assert result[0]["decision_type"] == "restart"
    assert result[0]["actor_identity_id"] == str(actor_canonical_id)
    assert result[0]["target_identity_id"] == str(target_canonical_id)

    assert len(ensure_edge_calls) == 2
    assert ensure_edge_calls[0][4] == "records_decision"
    assert ensure_edge_calls[1][4] == "records_action_on"

    assert evidence.canonical_entity_refs["decisions"] == result


@pytest.mark.asyncio
async def test_link_evidence_decisions_no_decisions():
    from contextedge.services.decision_service import link_evidence_decisions

    evidence = SimpleNamespace(
        id=uuid4(),
        domain_id=None,
        canonical_entity_refs={},
    )

    db, _ = _make_db()

    with patch(
        "contextedge.services.decision_service.extract_decisions",
        new_callable=AsyncMock,
        return_value=[],
    ):
        result = await link_evidence_decisions(
            db,
            tenant_id=uuid4(),
            evidence=evidence,
            content="No actions here, just a status report.",
        )

    assert result == []


@pytest.mark.asyncio
async def test_link_evidence_decisions_actor_only():
    """When target is empty, only records_decision edge is created."""
    from contextedge.services.decision_service import link_evidence_decisions

    actor_canonical_id = uuid4()
    evidence = SimpleNamespace(id=uuid4(), domain_id=None, canonical_entity_refs={})

    ensure_edge_calls = []

    async def mock_ensure_edge(db, tid, src_type, src_id, tgt_type, tgt_id, etype, **kw):
        ensure_edge_calls.append(etype)
        return GraphEdge(tenant_id=tid, source_node_type=src_type, source_node_id=src_id,
                         target_node_type=tgt_type, target_node_id=tgt_id, edge_type=etype)

    async def mock_resolve(db, tid, entities, source_id=None):
        return [{"canonical_id": actor_canonical_id, "canonical_name": "jsmith",
                 "entity_type": "person", "matched_via": "alias", "alias": "jsmith", "confidence": 0.9}]

    db, _ = _make_db()
    mock_decisions = [{"decision_type": "restart", "actor": "jsmith", "target": "", "action": "Restarted service", "context": ""}]

    with (
        patch("contextedge.services.decision_service.extract_decisions", new_callable=AsyncMock, return_value=mock_decisions),
        patch("contextedge.services.decision_service.ensure_edge", side_effect=mock_ensure_edge),
        patch("contextedge.services.decision_service.resolve_extracted_entities", side_effect=mock_resolve),
        patch("contextedge.services.decision_service.append_operational_event", new_callable=AsyncMock),
    ):
        result = await link_evidence_decisions(db, tenant_id=uuid4(), evidence=evidence,
                                               content="jsmith restarted the service")

    assert len(result) == 1
    assert result[0]["actor_identity_id"] == str(actor_canonical_id)
    assert result[0]["target_identity_id"] is None
    assert ensure_edge_calls == ["records_decision"]


@pytest.mark.asyncio
async def test_link_evidence_decisions_target_only():
    """When actor is empty, only records_action_on edge is created."""
    from contextedge.services.decision_service import link_evidence_decisions

    target_canonical_id = uuid4()
    evidence = SimpleNamespace(id=uuid4(), domain_id=None, canonical_entity_refs={})

    ensure_edge_calls = []

    async def mock_ensure_edge(db, tid, src_type, src_id, tgt_type, tgt_id, etype, **kw):
        ensure_edge_calls.append(etype)
        return GraphEdge(tenant_id=tid, source_node_type=src_type, source_node_id=src_id,
                         target_node_type=tgt_type, target_node_id=tgt_id, edge_type=etype)

    async def mock_resolve(db, tid, entities, source_id=None):
        return [{"canonical_id": target_canonical_id, "canonical_name": "vpn-gw",
                 "entity_type": "service", "matched_via": "new", "alias": "vpn-gw", "confidence": 0.8}]

    db, _ = _make_db()
    mock_decisions = [{"decision_type": "restart", "actor": "", "target": "vpn-gw", "action": "Gateway restarted", "context": ""}]

    with (
        patch("contextedge.services.decision_service.extract_decisions", new_callable=AsyncMock, return_value=mock_decisions),
        patch("contextedge.services.decision_service.ensure_edge", side_effect=mock_ensure_edge),
        patch("contextedge.services.decision_service.resolve_extracted_entities", side_effect=mock_resolve),
        patch("contextedge.services.decision_service.append_operational_event", new_callable=AsyncMock),
    ):
        result = await link_evidence_decisions(db, tenant_id=uuid4(), evidence=evidence,
                                               content="Gateway vpn-gw was restarted")

    assert len(result) == 1
    assert result[0]["actor_identity_id"] is None
    assert result[0]["target_identity_id"] == str(target_canonical_id)
    assert ensure_edge_calls == ["records_action_on"]


@pytest.mark.asyncio
async def test_link_evidence_decisions_failed_resolution():
    """When resolution returns empty, no edges are created but decision is still recorded."""
    from contextedge.services.decision_service import link_evidence_decisions

    evidence = SimpleNamespace(id=uuid4(), domain_id=None, canonical_entity_refs={})

    ensure_edge_calls = []

    async def mock_ensure_edge(db, tid, src_type, src_id, tgt_type, tgt_id, etype, **kw):
        ensure_edge_calls.append(etype)

    async def mock_resolve(db, tid, entities, source_id=None):
        return []

    db, _ = _make_db()
    mock_decisions = [{"decision_type": "approval", "actor": "unknown_person", "target": "unknown_system",
                       "action": "Approved change", "context": ""}]

    with (
        patch("contextedge.services.decision_service.extract_decisions", new_callable=AsyncMock, return_value=mock_decisions),
        patch("contextedge.services.decision_service.ensure_edge", side_effect=mock_ensure_edge),
        patch("contextedge.services.decision_service.resolve_extracted_entities", side_effect=mock_resolve),
        patch("contextedge.services.decision_service.append_operational_event", new_callable=AsyncMock),
    ):
        result = await link_evidence_decisions(db, tenant_id=uuid4(), evidence=evidence,
                                               content="Some person approved something on some system")

    assert len(result) == 1
    assert result[0]["actor_identity_id"] is None
    assert result[0]["target_identity_id"] is None
    assert len(ensure_edge_calls) == 0


@pytest.mark.asyncio
async def test_link_evidence_decisions_preserves_existing_identities():
    """Decisions key is merged alongside existing identities in canonical_entity_refs."""
    from contextedge.services.decision_service import link_evidence_decisions

    existing_identities = [{"canonical_id": str(uuid4()), "canonical_name": "jsmith"}]
    evidence = SimpleNamespace(
        id=uuid4(), domain_id=None,
        canonical_entity_refs={"identities": existing_identities},
    )

    async def mock_ensure_edge(db, tid, src_type, src_id, tgt_type, tgt_id, etype, **kw):
        return GraphEdge(tenant_id=tid, source_node_type=src_type, source_node_id=src_id,
                         target_node_type=tgt_type, target_node_id=tgt_id, edge_type=etype)

    async def mock_resolve(db, tid, entities, source_id=None):
        return [{"canonical_id": uuid4(), "canonical_name": "jsmith",
                 "entity_type": "person", "matched_via": "alias", "alias": "jsmith", "confidence": 0.9}]

    db, _ = _make_db()
    mock_decisions = [{"decision_type": "restart", "actor": "jsmith", "target": "", "action": "Restarted", "context": ""}]

    with (
        patch("contextedge.services.decision_service.extract_decisions", new_callable=AsyncMock, return_value=mock_decisions),
        patch("contextedge.services.decision_service.ensure_edge", side_effect=mock_ensure_edge),
        patch("contextedge.services.decision_service.resolve_extracted_entities", side_effect=mock_resolve),
        patch("contextedge.services.decision_service.append_operational_event", new_callable=AsyncMock),
    ):
        await link_evidence_decisions(db, tenant_id=uuid4(), evidence=evidence,
                                      content="jsmith restarted the service")

    assert "identities" in evidence.canonical_entity_refs
    assert evidence.canonical_entity_refs["identities"] == existing_identities
    assert "decisions" in evidence.canonical_entity_refs


# =========================================================================
# Tier 1: extractor edge case â€” LLM returns a list instead of dict
# =========================================================================

@pytest.mark.asyncio
async def test_extract_decisions_handles_list_response():
    """If llm_complete_json returns a list, extract_decisions returns it directly."""
    from contextedge.ai.extractors.decision_extractor import extract_decisions

    list_response = [
        {"decision_type": "restart", "actor": "jsmith", "target": "vpn-gw",
         "action": "Restarted gateway", "context": ""}
    ]
    with patch(
        "contextedge.ai.extractors.decision_extractor.llm_complete_json",
        new_callable=AsyncMock,
        return_value=list_response,
    ):
        result = await extract_decisions("Engineer jsmith restarted vpn-gw")

    assert result == list_response


# =========================================================================
# Tier 2: integration tests â€” execution_service calls ensure_edge
# =========================================================================

@pytest.mark.asyncio
async def test_start_execution_creates_executed_playbook_edge():
    """start_execution calls ensure_edge with session->playbook when session_id is set."""
    from contextedge.services.execution_service import start_execution

    tenant_id = uuid4()
    actor_id = uuid4()
    playbook_id = uuid4()
    session_id = uuid4()
    version_id = uuid4()

    playbook = SimpleNamespace(
        id=playbook_id, tenant_id=tenant_id,
        lifecycle_state="approved", automation_mode="human_confirmed",
        title="Test Playbook", expiry_at=None, approval_policy_id=None,
    )
    version = SimpleNamespace(
        id=version_id, playbook_id=playbook_id,
        published_at="2026-01-01T00:00:00Z", steps=[{"order": 1, "text": "Check service status", "safety_class": "read_only"}],
        semantic_version="1.0.0",
    )

    ensure_edge_calls = []

    async def capture_ensure_edge(db, tid, src_type, src_id, tgt_type, tgt_id, etype, **kw):
        ensure_edge_calls.append({"src_type": src_type, "tgt_type": tgt_type, "edge_type": etype,
                                  "src_id": src_id, "tgt_id": tgt_id})
        return GraphEdge(tenant_id=tid, source_node_type=src_type, source_node_id=src_id,
                         target_node_type=tgt_type, target_node_id=tgt_id, edge_type=etype)

    db, _ = _make_db()
    db.get = AsyncMock(
        side_effect=lambda cls, id: {
            "Playbook": playbook,
            "PlaybookVersion": version,
        }.get(cls.__name__)
    )

    with (
        patch("contextedge.services.execution_service.ensure_edge", side_effect=capture_ensure_edge),
        patch("contextedge.services.execution_service.append_operational_event", new_callable=AsyncMock),
        patch("contextedge.services.execution_service.append_trace_event", new_callable=AsyncMock),
        patch("contextedge.services.execution_service.create_decision", new_callable=AsyncMock),
    ):
        run = await start_execution(
            db, tenant_id=tenant_id, actor_id=actor_id,
            roles=["domain_admin"], playbook_id=playbook_id,
            playbook_version_id=version_id, session_id=session_id,
        )

    matching = [c for c in ensure_edge_calls if c["edge_type"] == "executed_playbook"]
    assert len(matching) == 1
    assert matching[0]["src_type"] == "session"
    assert matching[0]["src_id"] == session_id
    assert matching[0]["tgt_type"] == "playbook"
    assert matching[0]["tgt_id"] == playbook_id


@pytest.mark.asyncio
async def test_start_execution_no_edge_without_session():
    """start_execution does NOT create executed_playbook edge when session_id is None."""
    from contextedge.services.execution_service import start_execution

    tenant_id = uuid4()
    playbook_id = uuid4()
    version_id = uuid4()

    playbook = SimpleNamespace(
        id=playbook_id, tenant_id=tenant_id,
        lifecycle_state="approved", automation_mode="human_confirmed",
        title="Test Playbook", expiry_at=None, approval_policy_id=None,
    )
    version = SimpleNamespace(
        id=version_id, playbook_id=playbook_id,
        published_at="2026-01-01T00:00:00Z", steps=[{"order": 1, "text": "Check service status", "safety_class": "read_only"}],
        semantic_version="1.0.0",
    )

    ensure_edge_calls = []

    async def capture_ensure_edge(db, tid, src_type, src_id, tgt_type, tgt_id, etype, **kw):
        ensure_edge_calls.append(etype)

    db, _ = _make_db()
    db.get = AsyncMock(
        side_effect=lambda cls, id: {
            "Playbook": playbook,
            "PlaybookVersion": version,
        }.get(cls.__name__)
    )

    with (
        patch("contextedge.services.execution_service.ensure_edge", side_effect=capture_ensure_edge),
        patch("contextedge.services.execution_service.append_operational_event", new_callable=AsyncMock),
        patch("contextedge.services.execution_service.append_trace_event", new_callable=AsyncMock),
        patch("contextedge.services.execution_service.create_decision", new_callable=AsyncMock),
    ):
        await start_execution(
            db, tenant_id=tenant_id, actor_id=uuid4(),
            roles=["domain_admin"], playbook_id=playbook_id,
            playbook_version_id=version_id, session_id=None,
        )

    assert "executed_playbook" not in ensure_edge_calls


@pytest.mark.asyncio
async def test_decide_approval_creates_approved_by_edge():
    """decide_approval calls ensure_edge with approval_request->user."""
    from contextedge.services.execution_service import decide_approval

    tenant_id = uuid4()
    approval_id = uuid4()
    decided_by = uuid4()
    run_id = uuid4()

    req = SimpleNamespace(
        id=approval_id, tenant_id=tenant_id,
        status="pending", execution_run_id=run_id,
        step_run_id=None, safety_class="low_side_effect",
        requested_action="execute_step:0",
        decided_by=None, decided_at=None, decision_comment=None,
    )
    run = SimpleNamespace(
        id=run_id, tenant_id=tenant_id, status="awaiting_approval",
        session_id=None, playbook_id=None, initiated_by=uuid4(),
    )

    ensure_edge_calls = []

    async def capture_ensure_edge(db, tid, src_type, src_id, tgt_type, tgt_id, etype, **kw):
        ensure_edge_calls.append({"edge_type": etype, "src_type": src_type, "tgt_type": tgt_type, "tgt_id": tgt_id})
        return GraphEdge(tenant_id=tid, source_node_type=src_type, source_node_id=src_id,
                         target_node_type=tgt_type, target_node_id=tgt_id, edge_type=etype)

    db, _ = _make_db()
    db.get = AsyncMock(side_effect=lambda cls, id: req if id == approval_id else run)
    # F-15 row-lock: decide_approval now loads the approval via
    # db.execute(select(...).with_for_update()); mock accordingly.
    _approval_result = Mock()
    _approval_result.scalar_one_or_none.return_value = req
    db.execute = AsyncMock(return_value=_approval_result)

    with (
        patch("contextedge.services.execution_service.ensure_edge", side_effect=capture_ensure_edge),
        patch("contextedge.services.execution_service.append_operational_event", new_callable=AsyncMock),
        patch("contextedge.services.execution_service.create_decision", new_callable=AsyncMock),
    ):
        result = await decide_approval(
            db, tenant_id=tenant_id, approval_request_id=approval_id,
            decided_by=decided_by, decision="approved", comment="LGTM",
        )

    matching = [c for c in ensure_edge_calls if c["edge_type"] == "approved_by"]
    assert len(matching) == 1
    assert matching[0]["src_type"] == "approval_request"
    assert matching[0]["tgt_type"] == "user"
    assert matching[0]["tgt_id"] == decided_by


@pytest.mark.asyncio
async def test_decide_approval_creates_denied_by_edge():
    """decide_approval calls ensure_edge with denied_by when decision is denied."""
    from contextedge.services.execution_service import decide_approval

    tenant_id = uuid4()
    approval_id = uuid4()
    run_id = uuid4()

    req = SimpleNamespace(
        id=approval_id, tenant_id=tenant_id,
        status="pending", execution_run_id=run_id,
        step_run_id=None, safety_class="destructive",
        requested_action="execute_step:0",
        decided_by=None, decided_at=None, decision_comment=None,
    )
    run = SimpleNamespace(
        id=run_id, tenant_id=tenant_id, status="awaiting_approval",
        completed_at=None, outcome=None, outcome_summary=None,
        session_id=None, playbook_id=None, initiated_by=uuid4(),
    )

    ensure_edge_calls = []

    async def capture_ensure_edge(db, tid, src_type, src_id, tgt_type, tgt_id, etype, **kw):
        ensure_edge_calls.append(etype)
        return GraphEdge(tenant_id=tid, source_node_type=src_type, source_node_id=src_id,
                         target_node_type=tgt_type, target_node_id=tgt_id, edge_type=etype)

    db, _ = _make_db()
    db.get = AsyncMock(side_effect=lambda cls, id: req if id == approval_id else run)
    # F-15 row-lock (see same-test above).
    _approval_result = Mock()
    _approval_result.scalar_one_or_none.return_value = req
    db.execute = AsyncMock(return_value=_approval_result)

    with (
        patch("contextedge.services.execution_service.ensure_edge", side_effect=capture_ensure_edge),
        patch("contextedge.services.execution_service.append_operational_event", new_callable=AsyncMock),
        patch("contextedge.services.execution_service.create_decision", new_callable=AsyncMock),
    ):
        await decide_approval(
            db, tenant_id=tenant_id, approval_request_id=approval_id,
            decided_by=uuid4(), decision="denied", comment="Too risky",
        )

    assert "denied_by" in ensure_edge_calls


@pytest.mark.asyncio
async def test_complete_execution_creates_outcome_edge():
    """complete_execution calls ensure_edge with execution_run->playbook."""
    from contextedge.services.execution_service import complete_execution

    tenant_id = uuid4()
    run_id = uuid4()
    playbook_id = uuid4()

    run = SimpleNamespace(
        id=run_id, tenant_id=tenant_id,
        playbook_id=playbook_id, session_id=None,
        status="running", completed_at=None,
        outcome=None, outcome_summary=None,
    )

    ensure_edge_calls = []

    async def capture_ensure_edge(db, tid, src_type, src_id, tgt_type, tgt_id, etype, **kw):
        ensure_edge_calls.append({"edge_type": etype, "src_id": src_id, "tgt_id": tgt_id})
        return GraphEdge(tenant_id=tid, source_node_type=src_type, source_node_id=src_id,
                         target_node_type=tgt_type, target_node_id=tgt_id, edge_type=etype)

    db, _ = _make_db()
    db.get = AsyncMock(return_value=run)
    # complete_execution now performs an extra db.execute() to find the
    # execute_playbook Decision associated with this execution_run_id.
    db.execute = AsyncMock(return_value=_ScalarOneOrNoneResult(None))

    from contextedge.models.decision import Decision
    mock_list_result = _ScalarsProxy([])

    with (
        patch("contextedge.services.execution_service.ensure_edge", side_effect=capture_ensure_edge),
        patch("contextedge.services.execution_service.append_operational_event", new_callable=AsyncMock),
        patch("contextedge.services.execution_service.append_trace_event", new_callable=AsyncMock),
        patch("contextedge.services.execution_service.record_outcome", new_callable=AsyncMock),
        patch("contextedge.services.decision_trace_service.list_decisions", new_callable=AsyncMock, return_value=[]),
    ):
        await complete_execution(
            db, tenant_id=tenant_id, execution_run_id=run_id,
            outcome="success", outcome_summary="VPN restored",
        )

    matching = [c for c in ensure_edge_calls if c["edge_type"] == "execution_outcome"]
    assert len(matching) == 1
    assert matching[0]["src_id"] == run_id
    assert matching[0]["tgt_id"] == playbook_id

