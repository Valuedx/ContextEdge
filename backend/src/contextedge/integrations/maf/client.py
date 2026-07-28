"""Deployment-neutral client port used by the MAF adapter."""

from __future__ import annotations

from typing import Protocol

import httpx

from contextedge.graph.agent.contracts import (
    AgentGraphAccessScope,
    AgentGraphRequest,
    AgentGraphSubset,
)
from contextedge.graph.agent.service import AgentGraphProjectionService
from contextedge.graph.temporal import normalize_graph_as_of


class ContextGraphClient(Protocol):
    async def get_agent_subset(self, request: AgentGraphRequest) -> AgentGraphSubset: ...


class InProcessContextGraphClient:
    def __init__(
        self,
        service: AgentGraphProjectionService,
        scope: AgentGraphAccessScope,
    ):
        self.service = service
        self.scope = scope

    async def get_agent_subset(self, request: AgentGraphRequest) -> AgentGraphSubset:
        effective = request.model_copy(
            update={
                "domain_id": self.scope.domain_id,
                "as_of": normalize_graph_as_of(request.as_of),
            }
        )
        return await self.service.project(
            effective,
            self.scope,
            invocation_mode="maf",
        )


class HttpContextGraphClient:
    def __init__(
        self,
        base_url: str,
        *,
        bearer_token: str | None = None,
        service_token: str | None = None,
        timeout: float = 15.0,
        client: httpx.AsyncClient | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.bearer_token = bearer_token
        self.service_token = service_token
        self.timeout = timeout
        self.client = client

    async def get_agent_subset(self, request: AgentGraphRequest) -> AgentGraphSubset:
        headers: dict[str, str] = {}
        if self.bearer_token:
            headers["Authorization"] = f"Bearer {self.bearer_token}"
        if self.service_token:
            headers["X-Service-Token"] = self.service_token
        owns_client = self.client is None
        client = self.client or httpx.AsyncClient(timeout=self.timeout)
        try:
            response = await client.post(
                f"{self.base_url}/api/v1/graph/agent-subsets",
                json=request.model_dump(mode="json", exclude_none=True),
                headers=headers,
            )
            response.raise_for_status()
            return AgentGraphSubset.model_validate(response.json())
        finally:
            if owns_client:
                await client.aclose()
