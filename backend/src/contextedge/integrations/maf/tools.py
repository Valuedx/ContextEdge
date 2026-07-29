"""On-demand Context Graph function tool for MAF agents."""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from pydantic import Field, ValidationError

from contextedge.graph.agent.contracts import AgentGraphRequest, GraphNodeRef
from contextedge.integrations.maf._compat import FunctionInvocationContext, tool
from contextedge.integrations.maf.client import ContextGraphClient


def _tool_error(code: str, message: str) -> dict[str, Any]:
    """Structured, model-actionable error result (never a raw traceback)."""
    return {"error": {"code": code, "message": message}, "nodes": [], "relationships": []}


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
        # Model-supplied arguments are untrusted: clamp to the contract limits
        # and turn malformed values into structured errors the agent can fix,
        # never raw KeyError/ValueError tracebacks.
        node_refs: list[GraphNodeRef] = []
        for index, item in enumerate((seeds or [])[:20]):
            if not isinstance(item, dict) or "type" not in item or "id" not in item:
                return _tool_error(
                    "invalid_seed",
                    f"Seed #{index} must be an object with 'type' and 'id' keys.",
                )
            try:
                node_refs.append(
                    GraphNodeRef(type=str(item["type"]), id=UUID(str(item["id"])))
                )
            except (ValueError, ValidationError):
                return _tool_error(
                    "invalid_seed",
                    f"Seed #{index} has an invalid type or non-UUID id.",
                )

        clean_entities = [str(e)[:500] for e in (entities or [])[:20]]
        try:
            request = AgentGraphRequest(
                query=" ".join((query or "").split())[:4_000],
                seeds=node_refs,
                entities=clean_entities,
                max_depth=min(max(int(max_depth), 1), 3),
                profile="maf.v1",
            )
        except ValidationError as exc:
            return _tool_error("invalid_request", f"Request rejected: {exc.error_count()} invalid field(s).")

        subset = await self.client.get_agent_subset(request)
        return subset.model_dump(mode="json")
