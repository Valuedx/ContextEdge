"""Proactive Context Graph injection through a MAF ContextProvider."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import structlog

from contextedge.graph.agent.contracts import AgentGraphRequest
from contextedge.integrations.maf._compat import ContextProvider
from contextedge.integrations.maf.client import ContextGraphClient

logger = structlog.get_logger(__name__)


def _message_text(message: Any) -> str:
    text = getattr(message, "text", None)
    if isinstance(text, str):
        return text
    content = getattr(message, "content", None)
    if isinstance(content, str):
        return content
    return str(message)


class ContextGraphProvider(ContextProvider):
    source_id = "contextedge.context_graph.maf.v1"

    def __init__(
        self,
        client: ContextGraphClient,
        *,
        request_factory: Callable[[str], AgentGraphRequest] | None = None,
    ):
        super().__init__(self.source_id)
        self.client = client
        self.request_factory = request_factory or (
            lambda query: AgentGraphRequest(query=query, profile="maf.v1")
        )

    async def before_run(
        self,
        *,
        agent: Any,
        session: Any,
        context: Any,
        state: dict[str, Any],
    ) -> None:
        del agent, session, state
        messages = context.get_messages(
            exclude_sources={self.source_id},
            include_input=True,
        )
        query = "\n".join(_message_text(message) for message in messages[-4:])
        if not query.strip():
            return
        try:
            subset = await self.client.get_agent_subset(self.request_factory(query))
        except Exception as exc:
            logger.warning(
                "maf_context_graph_provider_unavailable",
                error_type=type(exc).__name__,
            )
            return
        if not subset.nodes:
            return
        payload = subset.model_dump(
            mode="json",
            exclude={
                "projection_id",
                "generated_at",
                "usage",
                "warnings",
                "truncation_reasons",
            },
        )
        context.extend_instructions(
            self.source_id,
            f"ContextEdge Context Graph ({subset.profile}):\n"
            f"{json.dumps(payload, separators=(',', ':'), ensure_ascii=True)}"
        )
