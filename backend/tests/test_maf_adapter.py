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
    assert "grounding_status" in context.instructions[0][1]
    assert client.requests[0].budget.max_nodes == 60


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


class LongMessageSessionContext(StubSessionContext):
    def get_messages(self, **kwargs):
        return [SimpleNamespace(text="incident context " * 1_000)]


@pytest.mark.asyncio
async def test_provider_truncates_long_conversations_instead_of_dropping_context():
    """Over-4k conversations must still get graph context (truncated query)."""
    client = StubClient()
    provider = ContextGraphProvider(client)
    context = LongMessageSessionContext()

    await provider.before_run(agent=object(), session=object(), context=context, state={})

    assert len(client.requests) == 1
    assert len(client.requests[0].query) <= 4_000
    assert context.instructions, "context should still be injected"


@pytest.mark.asyncio
async def test_provider_fences_untrusted_graph_content():
    client = StubClient()
    provider = ContextGraphProvider(client)
    context = StubSessionContext()

    await provider.before_run(agent=object(), session=object(), context=context, state={})

    injected = context.instructions[0][1]
    assert "<untrusted-data>" in injected
    assert "</untrusted-data>" in injected
    assert "not instructions" in injected


@pytest.mark.asyncio
async def test_tool_returns_structured_error_for_malformed_seeds():
    client = StubClient()
    toolset = ContextGraphTools(client)

    result = await toolset.query_context_graph.invoke(
        arguments={
            "query": "payment failure",
            "seeds": [{"type": "session", "id": "not-a-uuid"}],
        },
        skip_parsing=True,
    )

    assert result["error"]["code"] == "invalid_seed"
    assert result["nodes"] == []
    assert client.requests == []


@pytest.mark.asyncio
async def test_tool_returns_structured_error_for_missing_seed_keys():
    client = StubClient()
    toolset = ContextGraphTools(client)

    result = await toolset.query_context_graph.invoke(
        arguments={"query": "payment failure", "seeds": [{"id": str(uuid4())}]},
        skip_parsing=True,
    )

    assert result["error"]["code"] == "invalid_seed"
    assert client.requests == []


def test_http_playbook_client_rejects_plain_http_by_default():
    from contextedge.integrations.maf.playbook_client import HttpPlaybookRetrievalClient

    with pytest.raises(ValueError, match="https"):
        HttpPlaybookRetrievalClient("http://contextedge.internal", bearer_token="t")
    from contextedge.integrations.maf.client import HttpContextGraphClient

    with pytest.raises(ValueError, match="https"):
        HttpContextGraphClient("http://contextedge.internal", bearer_token="t")

    dev_client = HttpContextGraphClient(
        "http://localhost:8000", bearer_token="t", allow_insecure_http=True
    )
    assert dev_client.base_url == "http://localhost:8000"

    prod_client = HttpContextGraphClient("https://contextedge.internal")
    assert prod_client.base_url == "https://contextedge.internal"


def test_plugin_registers_cohort_and_edge_proposal_tools():
    """Toolsets that exist but never reach plugin.tools are dead code —
    the agent can only call what the bundle registers."""
    from contextedge.integrations.maf.plugin import ContextGraphMAFPlugin

    plugin = ContextGraphMAFPlugin(
        StubClient(),
        cohort_client=object(),
        edge_proposal_client=object(),
    )
    assert plugin.cohort_toolset is not None
    assert plugin.edge_proposal_toolset is not None
    # The @tool decorator mints a fresh FunctionTool per attribute
    # access, so registration is checked by name, not identity.
    names = {t.name for t in plugin.tools}
    assert "get_cohort_shared_attributes" in names
    assert "propose_dependency" in names


def test_plugin_without_optional_clients_registers_core_tool_only():
    from contextedge.integrations.maf.plugin import ContextGraphMAFPlugin

    plugin = ContextGraphMAFPlugin(StubClient())
    assert plugin.cohort_toolset is None
    assert plugin.edge_proposal_toolset is None
    assert len(plugin.tools) == 1


def test_plugin_registers_playbook_tools():
    from contextedge.integrations.maf.plugin import ContextGraphMAFPlugin

    plugin = ContextGraphMAFPlugin(StubClient(), playbook_client=object())
    names = {t.name for t in plugin.tools}
    assert "match_playbooks" in names
    assert "get_playbook" in names
    assert "check_trigger_conditions" in names
    assert "get_negative_knowledge" in names


def test_plugin_threads_writeback_to_the_provider():
    """The advertised flywheel must be reachable through the bundle,
    not only by hand-building ContextGraphProvider."""
    from contextedge.integrations.maf.plugin import ContextGraphMAFPlugin

    sentinel = object()
    plugin = ContextGraphMAFPlugin(StubClient(), writeback=sentinel)
    assert plugin.provider is not None
    assert plugin.provider.writeback is sentinel
    # And omitting it keeps the provider read-only, as before.
    assert ContextGraphMAFPlugin(StubClient()).provider.writeback is None
