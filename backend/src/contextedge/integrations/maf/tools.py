"""On-demand Context Graph function tool for MAF agents."""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from pydantic import Field

from contextedge.graph.agent.contracts import AgentGraphRequest, GraphNodeRef
from contextedge.integrations.maf._compat import FunctionInvocationContext, tool
from contextedge.integrations.maf.client import ContextGraphClient


class ContextGraphTools:
    def __init__(self, client: ContextGraphClient):
        self.client = client

    @tool(
        name="query_context_graph",
        description=(
            "Retrieve a bounded ContextEdge graph subset relevant to the current "
            "operational question."
        ),
    )
    async def query_context_graph(
        self,
        query: Annotated[
            str,
            Field(description="Operational question or task to retrieve context for."),
        ],
        seeds: Annotated[
            list[dict[str, str]] | None,
            Field(description="Optional graph seeds with type and UUID id."),
        ] = None,
        entities: Annotated[
            list[str] | None,
            Field(description="Optional operational entity names."),
        ] = None,
        max_depth: Annotated[
            int,
            Field(ge=1, le=3, description="Maximum relationship depth."),
        ] = 2,
        context: FunctionInvocationContext | None = None,
    ) -> dict[str, Any]:
        del context
        node_refs = [
            GraphNodeRef(type=item["type"], id=UUID(item["id"]))
            for item in (seeds or [])
        ]
        subset = await self.client.get_agent_subset(
            AgentGraphRequest(
                query=query,
                seeds=node_refs,
                entities=entities or [],
                max_depth=max_depth,
                profile="maf.v1",
            )
        )
        return subset.model_dump(mode="json")
