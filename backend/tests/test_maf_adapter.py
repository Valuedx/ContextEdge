from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

pytest.importorskip("agent_framework")

from contextedge.graph.agent.contracts import (  # noqa: E402
    AgentGraphBudget,
    AgentGraphNode,
    AgentGraphProvenance,
    AgentGraphSubset,
    AgentGraphUsage,
)
from contextedge.integrations.maf.provider import ContextGraphProvider  # noqa: E402
from contextedge.integrations.maf.tools import ContextGraphTools  # noqa: E402


def _subset():
    node_id = uuid4()
    return AgentGraphSubset(
        profile="maf.v1",
        projection_id=uuid4(),
        generated_at=datetime.now(UTC),
        query="payment failure",
        seeds=[],
        nodes=[
            AgentGraphNode(
                key=f"session:{node_id}",
                type="session",
                id=node_id,
                label="Payment incident",
                facts={"status": "open"},
                relevance=1.0,
                provenance=AgentGraphProvenance(source_type="session"),
            )
        ],
        relationships=[],
        budget=AgentGraphBudget(),
        usage=AgentGraphUsage(nodes=1, relationships=0, characters=100),
    )


class StubClient:
    def __init__(self):
        self.requests = []

    async def get_agent_subset(self, request):
        self.requests.append(request)
        return _subset()


class StubSessionContext:
    def __init__(self):
        self.instructions = []

    def get_messages(self, **kwargs):
        assert kwargs["include_input"] is True
        return [SimpleNamespace(text="Why did the payment fail?")]

    def extend_instructions(self, source_id, instructions):
        self.instructions.append((source_id, instructions))


@pytest.mark.asyncio
async def test_provider_injects_attributed_context():
    client = StubClient()
    provider = ContextGraphProvider(client)
    context = StubSessionContext()

    await provider.before_run(
        agent=object(),
        session=object(),
        context=context,
        state={},
    )

    assert provider.source_id == "contextedge.context_graph.maf.v1"
    assert client.requests[0].profile == "maf.v1"
    assert context.instructions[0][0] == provider.source_id
    assert "Payment incident" in context.instructions[0][1]


@pytest.mark.asyncio
async def test_tool_invokes_through_maf_function_tool():
    client = StubClient()
    toolset = ContextGraphTools(client)

    result = await toolset.query_context_graph.invoke(
        arguments={
            "query": "payment failure",
            "entities": ["payment workflow"],
            "max_depth": 2,
        },
        skip_parsing=True,
    )

    assert result["profile"] == "maf.v1"
    assert result["nodes"][0]["label"] == "Payment incident"
    assert client.requests[0].entities == ["payment workflow"]
