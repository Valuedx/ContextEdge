from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from contextedge.deps import CurrentUser
from contextedge.graph.agent.contracts import (
    AgentGraphBudget,
    AgentGraphNode,
    AgentGraphProvenance,
    AgentGraphSubset,
    AgentGraphUsage,
)
from contextedge.integrations.maf.runtime import run_diagnose, run_playbook_tool_turn
from datetime import UTC, datetime


def _user():
    return CurrentUser(
        user_id=uuid4(),
        tenant_id=uuid4(),
        email="a@b.c",
        roles=["knowledge_manager"],
    )


def _subset():
    node_id = uuid4()
    return AgentGraphSubset(
        profile="maf.v1",
        projection_id=uuid4(),
        generated_at=datetime.now(UTC),
        query="vpn",
        seeds=[],
        nodes=[
            AgentGraphNode(
                key=f"playbook:{node_id}",
                type="playbook",
                id=node_id,
                label="Renew cert",
                facts={},
                relevance=1.0,
                provenance=AgentGraphProvenance(source_type="playbook"),
            )
        ],
        relationships=[],
        budget=AgentGraphBudget(),
        usage=AgentGraphUsage(nodes=1, relationships=0, characters=40),
    )


def _ranked(pb_id, ver_id):
    return SimpleNamespace(
        playbook=SimpleNamespace(id=pb_id, title="Renew VPN cert", stable_key="vpn-cert"),
        playbook_version_id=ver_id,
        semantic_version="1.2.0",
        applicability="strong",
        applicability_factors=["vpn"],
        applicability_differences=None,
        selection_margin=0.1,
        confidence_calibrated=0.62,
        score=0.62,
    )


@pytest.mark.asyncio
async def test_run_diagnose_returns_ranked_playbook_and_grounding():
    pb_id, ver_id = uuid4(), uuid4()
    ranked = _ranked(pb_id, ver_id)
    graph = SimpleNamespace(get_agent_subset=AsyncMock(return_value=_subset()))
    playbook_client = object()

    with (
        patch(
            "contextedge.integrations.maf.runtime.build_diagnose_bundle",
            AsyncMock(
                return_value=SimpleNamespace(
                    graph_client=graph,
                    playbook_client=playbook_client,
                    plugin=None,
                    writeback=None,
                    frame=SimpleNamespace(
                        symptom_text="vpn login failing",
                        lexical_terms=["vpn"],
                        error_signature_id=None,
                        issue_signature_id=None,
                        ci_entity_ids=[],
                    ),
                    scope=object(),
                )
            ),
        ),
        patch(
            "contextedge.integrations.maf.runtime.rank_playbooks",
            AsyncMock(return_value=[ranked]),
        ),
        patch(
            "contextedge.integrations.maf.runtime.effective_max_risk_tier",
            return_value="high",
        ),
        patch(
            "contextedge.integrations.maf.runtime.llm_complete",
            create=True,
            new=AsyncMock(side_effect=RuntimeError("no llm")),
        ),
        patch(
            "contextedge.ai.provider.llm_complete",
            AsyncMock(side_effect=RuntimeError("no llm")),
        ),
    ):
        result = await run_diagnose(
            AsyncMock(),
            _user(),
            symptoms=["vpn login failing"],
            entities=["vpn-gw"],
        )

    assert result["playbook_id"] == str(pb_id)
    assert result["playbook_version_id"] == str(ver_id)
    assert result["applicability"] == "strong"
    assert result["grounding_status"] in {"grounded", "weak"}
    assert result["cited_node_keys"]
    assert "chosen_playbook_version_id=" in result["rationale"]
    request = graph.get_agent_subset.await_args.args[0]
    assert request.budget.max_nodes == 60
    assert request.budget.max_characters == 30_000


@pytest.mark.asyncio
async def test_run_diagnose_runs_tools_and_writeback():
    pb_id, ver_id = uuid4(), uuid4()
    ranked = _ranked(pb_id, ver_id)
    graph = SimpleNamespace(get_agent_subset=AsyncMock(return_value=_subset()))
    playbook_client = SimpleNamespace(
        match_playbooks=AsyncMock(
            return_value=[
                {
                    "playbook_id": str(pb_id),
                    "playbook_title": "Renew VPN cert",
                    "playbook_version_id": str(ver_id),
                    "applicability": "strong",
                    "confidence_calibrated": 0.62,
                }
            ]
        ),
        check_trigger_conditions=AsyncMock(
            return_value={"level": "strong", "drop": False}
        ),
        get_playbook=AsyncMock(
            return_value={
                "steps": [
                    {
                        "title": "Renew",
                        "safety_class": "low_side_effect",
                        "requires_approval": False,
                        "reversible": True,
                        "rollback_hint": "restore previous cert",
                        "verification": True,
                        "tool_ref": "cert-manager",
                        "inputs": {},
                    }
                ]
            }
        ),
        get_negative_knowledge=AsyncMock(return_value={"items": []}),
    )
    writeback = SimpleNamespace(record_decision=AsyncMock(return_value={"id": "d1"}))
    provider = SimpleNamespace(after_run=AsyncMock())
    llm_complete = AsyncMock(
        return_value=(
            "Use the cert playbook.\n"
            f"chosen_playbook_version_id={ver_id}\n"
            "cited_node_keys=none\n"
            "applicability=strong"
        )
    )

    with (
        patch(
            "contextedge.integrations.maf.runtime.build_diagnose_bundle",
            AsyncMock(
                return_value=SimpleNamespace(
                    graph_client=graph,
                    playbook_client=playbook_client,
                    plugin=SimpleNamespace(provider=provider, playbook_toolset=None),
                    writeback=writeback,
                    frame=SimpleNamespace(
                        symptom_text="vpn login failing",
                        lexical_terms=["vpn"],
                        error_signature_id=None,
                        issue_signature_id=None,
                        ci_entity_ids=[],
                    ),
                    scope=object(),
                )
            ),
        ),
        patch(
            "contextedge.integrations.maf.runtime.rank_playbooks",
            AsyncMock(return_value=[ranked]),
        ),
        patch(
            "contextedge.integrations.maf.runtime.effective_max_risk_tier",
            return_value="high",
        ),
        patch(
            "contextedge.ai.provider.llm_complete",
            llm_complete,
        ),
    ):
        result = await run_diagnose(
            AsyncMock(),
            _user(),
            symptoms=["vpn login failing"],
            entities=["vpn-gw"],
        )

    names = [row["tool"] for row in result["tool_calls"]]
    assert names == [
        "match_playbooks",
        "check_trigger_conditions",
        "get_playbook",
        "get_negative_knowledge",
    ]
    playbook_client.match_playbooks.assert_awaited()
    playbook_client.get_playbook.assert_awaited()
    provider.after_run.assert_awaited()
    assert result["agent_mode"] == "tools"
    assert "chosen_playbook_version_id" in result["rationale"]
    llm_complete.assert_awaited()
    assert llm_complete.await_args.kwargs["task"] == "diagnose"


def test_diagnose_task_does_not_use_extraction_model(monkeypatch):
    from contextedge.ai import provider as llm_provider
    from contextedge.config import settings

    monkeypatch.setattr(settings, "default_extraction_model", "openai/gemma4")
    monkeypatch.setattr(settings, "diagnose_model", "vertex_ai/gemini-2.5-flash")
    monkeypatch.setitem(
        llm_provider.MODEL_ROUTING, "extraction", settings.default_extraction_model
    )
    monkeypatch.setitem(
        llm_provider.MODEL_ROUTING, "diagnose", settings.diagnose_model
    )
    assert llm_provider.get_model_for_task("extraction") == "openai/gemma4"
    assert llm_provider.get_model_for_task("diagnose") == "vertex_ai/gemini-2.5-flash"


@pytest.mark.asyncio
async def test_playbook_tool_turn_abstains_when_trigger_drops():
    client = SimpleNamespace(
        match_playbooks=AsyncMock(
            return_value=[
                {
                    "playbook_id": str(uuid4()),
                    "playbook_version_id": str(uuid4()),
                }
            ]
        ),
        check_trigger_conditions=AsyncMock(
            return_value={"level": "contradicted", "drop": True}
        ),
        get_playbook=AsyncMock(),
        get_negative_knowledge=AsyncMock(),
    )
    matches, chosen, calls = await run_playbook_tool_turn(
        client,
        symptoms=["x"],
        entities=[],
        environment={},
        top_k=3,
    )
    assert matches
    assert chosen["dropped"] is True
    assert [c["tool"] for c in calls] == [
        "match_playbooks",
        "check_trigger_conditions",
    ]
    client.get_playbook.assert_not_awaited()


@pytest.mark.asyncio
async def test_diagnose_endpoint_respects_flag(monkeypatch):
    from contextedge.api.v1 import agent as agent_api

    monkeypatch.setattr(agent_api.settings, "agent_diagnose_enabled", False)
    with pytest.raises(HTTPException) as exc:
        await agent_api.diagnose(
            agent_api.DiagnoseRequest(symptoms=["x"]),
            AsyncMock(),
            _user(),
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_trigger_check_and_negative_knowledge_routes_delegate():
    from contextedge.api.v1 import agent as agent_api

    client = SimpleNamespace(
        check_trigger_conditions=AsyncMock(return_value={"level": "exact"}),
        get_negative_knowledge=AsyncMock(return_value={"items": []}),
    )
    version_id = uuid4()
    with patch(
        "contextedge.api.v1.agent._playbook_client_for",
        return_value=client,
    ):
        trigger = await agent_api.trigger_check(
            agent_api.TriggerCheckRequest(
                playbook_version_id=version_id,
                environment={"os": "win"},
                symptoms=["vpn"],
            ),
            AsyncMock(),
            _user(),
        )
        nk = await agent_api.agent_negative_knowledge(
            version_id, AsyncMock(), _user()
        )
    assert trigger == {"level": "exact"}
    assert nk == {"items": []}
    client.check_trigger_conditions.assert_awaited_once()
    client.get_negative_knowledge.assert_awaited_once()
