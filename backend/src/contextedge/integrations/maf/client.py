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


class CmdbTopologyClient(Protocol):
    """Port for the cmdb_topology tool — live CI neighborhood lookups
    (ServiceNow as source of truth, ContextEdge as write-through cache)."""

    async def lookup(self, term: str) -> dict: ...


class InProcessCmdbTopologyClient:
    """Server-side implementation: opens its own session per lookup so the
    tool call commits (or discards) independently of any request session."""

    def __init__(self, session_factory, tenant_id):
        self.session_factory = session_factory
        self.tenant_id = tenant_id

    async def lookup(self, term: str) -> dict:
        from contextedge.services.cmdb_topology_service import lookup_topology

        async with self.session_factory() as db:
            try:
                result = await lookup_topology(db, self.tenant_id, term)
                await db.commit()
                return result
            except Exception:
                await db.rollback()
                raise


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
        allow_insecure_http: bool = False,
    ):
        self.base_url = base_url.rstrip("/")
        scheme = self.base_url.split("://", 1)[0].lower() if "://" in self.base_url else ""
        if scheme != "https" and not (
            allow_insecure_http and scheme == "http"
        ):
            # Tokens travel in headers; refuse to send them over plain HTTP
            # unless the caller opts in (local development).
            raise ValueError(
                "HttpContextGraphClient requires an https:// base_url; pass "
                "allow_insecure_http=True to use http:// in local development."
            )
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
