"""On-demand Context Graph function tool for MAF agents."""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from pydantic import Field, ValidationError

from contextedge.graph.agent.contracts import AgentGraphRequest, GraphNodeRef
from contextedge.integrations.maf._compat import FunctionInvocationContext, tool
from contextedge.integrations.maf.client import (
    ChangeRiskClient,
    CmdbTopologyClient,
    ContextGraphClient,
)


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
            # Must precede (TypeError, ValueError): pydantic's
            # ValidationError subclasses ValueError.
            return _tool_error("invalid_request", f"Request rejected: {exc.error_count()} invalid field(s).")
        except (TypeError, ValueError) as exc:
            # int("unlimited") etc. — model-supplied garbage, not a crash.
            return _tool_error("invalid_request", f"max_depth must be an integer 1-3 ({type(exc).__name__}).")

        subset = await self.client.get_agent_subset(request)
        return subset.model_dump(mode="json")


class CmdbTopologyTools:
    def __init__(self, client: CmdbTopologyClient):
        self.client = client

    @tool(
        name="cmdb_topology",
        description=(
            "Look up a configuration item's direct CMDB neighborhood from "
            "ServiceNow (a lookup made within the last few minutes serves "
            "the identical cached view; if ServiceNow is unreachable the "
            "last cached view is returned marked stale with its as_of "
            "time). Accepts a CI display name (e.g. 'vpn-gw-east-01') or a "
            "32-hex sys_id. Each neighbor's center_role says which side "
            "the queried CI is on: center_role 'parent' with relationship "
            "'depends_on' means the queried CI depends on that neighbor."
        ),
    )
    async def cmdb_topology(
        self,
        ci: Annotated[
            str,
            Field(description="CI display name or ServiceNow sys_id (32 hex chars)."),
        ],
        context: FunctionInvocationContext | None = None,
    ) -> dict[str, Any]:
        del context
        term = " ".join(str(ci or "").split())[:500]
        if not term:
            return {"error": {"code": "invalid_ci", "message": "Provide a CI name or sys_id."}}
        try:
            return await self.client.lookup(term)
        except Exception as exc:
            # Structured, model-actionable — never a raw traceback.
            return {
                "error": {
                    "code": "topology_unavailable",
                    "message": f"Topology lookup failed ({type(exc).__name__}).",
                }
            }


class ChangeRiskTools:
    def __init__(self, client: ChangeRiskClient):
        self.client = client

    @tool(
        name="assess_change_risk",
        description=(
            "Deterministic change-risk profile for a configuration item "
            "from ingested operational history: how often past changes on "
            "it were blamed for incidents (caused_by references), incident "
            "pressure and alert activity in the window, and the cached "
            "blast radius (dependents). Returns risk_level low/medium/high "
            "with a factors list explaining every contributing signal. Use "
            "BEFORE recommending or approving a change to a CI. Accepts a "
            "CI display name or 32-hex sys_id."
        ),
    )
    async def assess_change_risk(
        self,
        ci: Annotated[
            str,
            Field(description="CI display name or ServiceNow sys_id (32 hex chars)."),
        ],
        window_days: Annotated[
            int,
            Field(ge=1, le=730, description="History window in days (default 180)."),
        ] = 180,
        context: FunctionInvocationContext | None = None,
    ) -> dict[str, Any]:
        del context
        term = " ".join(str(ci or "").split())[:500]
        if not term:
            return {"error": {"code": "invalid_ci", "message": "Provide a CI name or sys_id."}}
        try:
            window = min(max(int(window_days), 1), 730)
        except (TypeError, ValueError):
            window = 180
        try:
            return await self.client.assess(term, window)
        except Exception as exc:
            return {
                "error": {
                    "code": "risk_assessment_unavailable",
                    "message": f"Change-risk assessment failed ({type(exc).__name__}).",
                }
            }
