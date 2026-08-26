"""Playbook retrieval tools for the diagnose agent."""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from pydantic import Field, ValidationError

from contextedge.integrations.maf._compat import FunctionInvocationContext, tool


def _tool_error(code: str, message: str) -> dict[str, Any]:
    return {"error": {"code": code, "message": message}}


def _parse_uuid(value: Any, field: str) -> UUID | dict[str, Any]:
    try:
        return UUID(str(value))
    except (ValueError, TypeError, ValidationError):
        return _tool_error("invalid_id", f"{field} must be a UUID.")


class PlaybookTools:
    def __init__(self, client):
        self.client = client

    @tool(
        name="match_playbooks",
        description=(
            "Rank approved playbooks for this incident. Returns candidates with "
            "playbook_version_id, applicability, breakdown and selection_margin. "
            "An empty list means abstain — do NOT invent a playbook."
        ),
    )
    async def match_playbooks(
        self,
        symptoms: Annotated[
            list[str],
            Field(description="Symptom strings from the ticket."),
        ],
        entities: Annotated[
            list[str] | None,
            Field(description="CI / identity names mentioned in the ticket."),
        ] = None,
        environment: Annotated[
            dict | None,
            Field(description="Environment facts used by trigger gating."),
        ] = None,
        top_k: Annotated[int, Field(ge=1, le=20)] = 5,
        context: FunctionInvocationContext | None = None,
    ) -> dict[str, Any]:
        del context
        results = await self.client.match_playbooks(
            [str(s)[:500] for s in (symptoms or [])[:20]],
            [str(e)[:500] for e in (entities or [])[:20]],
            dict(environment or {}),
            int(top_k),
        )
        return {"results": results, "empty_means": "No grounded playbook — abstain."}

    @tool(
        name="get_playbook",
        description=(
            "Return the full structured steps for a scored playbook version. "
            "version_id is required and must be the id from match_playbooks — "
            "never a different version. Truncated graph node facts are not a "
            "substitute for this call."
        ),
    )
    async def get_playbook(
        self,
        playbook_id: Annotated[str, Field(description="Playbook UUID.")],
        version_id: Annotated[
            str,
            Field(description="Published playbook_version_id from match_playbooks."),
        ],
        context: FunctionInvocationContext | None = None,
    ) -> dict[str, Any]:
        del context
        pb = _parse_uuid(playbook_id, "playbook_id")
        if isinstance(pb, dict):
            return pb
        ver = _parse_uuid(version_id, "version_id")
        if isinstance(ver, dict):
            return ver
        return await self.client.get_playbook(pb, ver)

    @tool(
        name="check_trigger_conditions",
        description=(
            "Deterministic applicability verdict for a playbook version. "
            "The agent verifies; it does not judge applicability itself. "
            "level=contradicted or drop=true means do not recommend."
        ),
    )
    async def check_trigger_conditions(
        self,
        playbook_version_id: Annotated[str, Field(description="Published version UUID.")],
        environment: Annotated[dict | None, Field(default=None)] = None,
        symptoms: Annotated[list[str] | None, Field(default=None)] = None,
        context: FunctionInvocationContext | None = None,
    ) -> dict[str, Any]:
        del context
        ver = _parse_uuid(playbook_version_id, "playbook_version_id")
        if isinstance(ver, dict):
            return ver
        return await self.client.check_trigger_conditions(
            ver,
            dict(environment or {}),
            [str(s)[:500] for s in (symptoms or [])[:20]],
        )

    @tool(
        name="get_negative_knowledge",
        description=(
            "What NOT to do for this playbook version, with sources. "
            "An empty items list means no linked anti-patterns — do NOT invent them."
        ),
    )
    async def get_negative_knowledge(
        self,
        playbook_version_id: Annotated[str, Field(description="Published version UUID.")],
        context: FunctionInvocationContext | None = None,
    ) -> dict[str, Any]:
        del context
        ver = _parse_uuid(playbook_version_id, "playbook_version_id")
        if isinstance(ver, dict):
            return ver
        return await self.client.get_negative_knowledge(ver)
