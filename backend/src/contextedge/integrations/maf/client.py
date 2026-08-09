"""Deployment-neutral client port used by the MAF adapter."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

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


class ChangeRiskClient(Protocol):
    """Port for the assess_change_risk tool — deterministic risk profile
    for a CI from ingested operational history (read-only)."""

    async def assess(self, ci: str, window_days: int) -> dict: ...


class FixApplicabilityClient(Protocol):
    """Port for the assess_fix_applicability tool — deterministic
    precondition matching of known fixes against a target CI
    (read-only)."""

    async def assess(self, ci: str) -> dict: ...


class InProcessChangeRiskClient:
    def __init__(self, session_factory, tenant_id):
        self.session_factory = session_factory
        self.tenant_id = tenant_id

    async def assess(self, ci: str, window_days: int) -> dict:
        from contextedge.services.change_risk_service import assess_change_risk

        async with self.session_factory() as db:
            # Read-only — nothing to commit; rollback on exit is harmless.
            return await assess_change_risk(
                db, self.tenant_id, ci, window_days=window_days
            )


class InProcessFixApplicabilityClient:
    def __init__(self, session_factory, tenant_id):
        self.session_factory = session_factory
        self.tenant_id = tenant_id

    async def assess(self, ci: str) -> dict:
        from contextedge.services.cmdb_topology_service import resolve_ci_entity
        from contextedge.services.fix_applicability_service import (
            assess_fix_applicability,
        )

        async with self.session_factory() as db:
            # Read-only — nothing to commit; rollback on exit is harmless.
            entity = await resolve_ci_entity(db, self.tenant_id, ci)
            if entity is None:
                return {
                    "error": {
                        "code": "ci_not_found",
                        "message": f"No CI matches {ci!r} for this tenant.",
                    }
                }
            return await assess_fix_applicability(db, self.tenant_id, entity)


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


class CohortClient(Protocol):
    """Port for get_cohort_shared_attributes (blueprint §1.6 #2)."""

    async def shared_attributes(self, evidence_ids: list) -> dict: ...


class InProcessCohortClient:
    def __init__(self, session_factory, tenant_id):
        self.session_factory = session_factory
        self.tenant_id = tenant_id

    async def shared_attributes(self, evidence_ids: list) -> dict:
        from contextedge.services.cohort_service import get_cohort_shared_attributes

        async with self.session_factory() as db:
            # Read-only — nothing to commit.
            return await get_cohort_shared_attributes(db, self.tenant_id, evidence_ids)


class EdgeProposalClient(Protocol):
    """Port for propose_dependency: agent-discovered topology enters as
    a REVIEWABLE proposal (proposed_depends_on, not in the maf.v1
    allowlist), never as authored fact."""

    async def propose(self, source_ci: str, target_ci: str, rationale: str, evidence_ids: list) -> dict: ...


class InProcessEdgeProposalClient:
    """``domain_id``: the deployment's domain, stamped onto proposals so
    domain-scoped review works. Without it, the proposal falls back to
    the CIs' own domain when both agree, else lands domainless (visible
    only to tenant-wide reviewers)."""

    def __init__(self, session_factory, tenant_id, *, domain_id=None):
        self.session_factory = session_factory
        self.tenant_id = tenant_id
        self.domain_id = domain_id

    async def propose(self, source_ci: str, target_ci: str, rationale: str, evidence_ids: list) -> dict:
        from contextedge.graph.builder import ensure_edge
        from contextedge.services.cmdb_topology_service import resolve_ci_entity_checked

        async with self.session_factory() as db:
            try:
                src, src_ambiguous = await resolve_ci_entity_checked(
                    db, self.tenant_id, source_ci
                )
                dst, dst_ambiguous = await resolve_ci_entity_checked(
                    db, self.tenant_id, target_ci
                )
                if src_ambiguous or dst_ambiguous:
                    which = source_ci if src_ambiguous else target_ci
                    return {"error": {"code": "ambiguous_ci",
                                      "message": f"Multiple CIs match {which!r}; use a sys_id."}}
                if src is None or dst is None:
                    missing = source_ci if src is None else target_ci
                    return {"error": {"code": "ci_not_found", "message": f"No CI matches {missing!r}."}}
                if src.id == dst.id:
                    return {"error": {"code": "self_edge", "message": "A CI cannot depend on itself."}}
                domain_id = self.domain_id
                if domain_id is None and src.domain_id == dst.domain_id:
                    domain_id = src.domain_id
                edge = await ensure_edge(
                    db,
                    self.tenant_id,
                    source_type="entity",
                    source_id=src.id,
                    target_type="entity",
                    target_id=dst.id,
                    edge_type="proposed_depends_on",
                    weight=1.0,
                    confidence=0.3,
                    metadata={
                        "origin": "agent_discovered",
                        "rationale": str(rationale)[:500],
                        "evidence_ids": [str(e) for e in evidence_ids[:10]],
                    },
                    domain_id=domain_id,
                )
                await db.commit()
                return {"status": "proposed", "edge_id": str(edge.id),
                        "source": src.name, "target": dst.name}
            except Exception:
                await db.rollback()
                raise


class DecisionWritebackClient(Protocol):
    """Port for F1 agent decision write-back: the MAF agent's diagnostic
    trail flows back into ContextEdge through the SAME decisions path
    humans use, so governance (review, audit, supersession) applies to
    agent-authored records identically."""

    async def record_decision(self, payload: dict) -> dict | None: ...


class InProcessDecisionWritebackClient:
    """``session_id`` / ``domain_id`` scope every recorded decision to
    the resolution session and domain the deployment is running for —
    without them an agent-authored decision floats free of the session
    trail and domain RBAC it should belong to."""

    def __init__(
        self,
        session_factory,
        tenant_id,
        actor_id,
        *,
        session_id=None,
        domain_id=None,
    ):
        self.session_factory = session_factory
        self.tenant_id = tenant_id
        self.actor_id = actor_id
        self.session_id = session_id
        self.domain_id = domain_id

    async def record_decision(self, payload: dict) -> dict | None:
        from contextedge.services.decision_trace_service import create_decision

        def _uuid_or_none(value):
            try:
                return UUID(str(value)) if value else None
            except ValueError:
                return None

        async with self.session_factory() as db:
            try:
                decision = await create_decision(
                    db,
                    tenant_id=self.tenant_id,
                    decision_type=payload.get("decision_type", "agent_diagnosis"),
                    agent_step=payload.get("agent_step", "maf_run"),
                    actor_type="ai",
                    actor_id=self.actor_id,
                    session_id=_uuid_or_none(payload.get("session_id")) or self.session_id,
                    domain_id=_uuid_or_none(payload.get("domain_id")) or self.domain_id,
                    context_snapshot=payload.get("context_snapshot", {}),
                    evidence_refs=payload.get("evidence_refs") or [],
                    rationale_summary=payload.get("rationale_summary", ""),
                    confidence=payload.get("confidence"),
                    approval_required=bool(payload.get("approval_required", True)),
                )
                await db.commit()
                return {"id": str(decision.id)}
            except Exception:
                await db.rollback()
                raise


class HttpDecisionWritebackClient:
    """POST /api/v1/decisions with the same token hygiene as the other
    HTTP clients: credentials travel in headers, so plain HTTP is
    refused unless local development opts in."""

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
        if scheme != "https" and not (allow_insecure_http and scheme == "http"):
            raise ValueError(
                "HttpDecisionWritebackClient requires an https:// base_url; pass "
                "allow_insecure_http=True to use http:// in local development."
            )
        self.bearer_token = bearer_token
        self.service_token = service_token
        self.timeout = timeout
        self.client = client

    async def record_decision(self, payload: dict) -> dict | None:
        headers: dict[str, str] = {}
        if self.bearer_token:
            headers["Authorization"] = f"Bearer {self.bearer_token}"
        if self.service_token:
            headers["X-Service-Token"] = self.service_token
        owns_client = self.client is None
        client = self.client or httpx.AsyncClient(timeout=self.timeout)
        try:
            response = await client.post(
                f"{self.base_url}/api/v1/decisions",
                json=payload,
                headers=headers,
            )
            response.raise_for_status()
            return response.json()
        finally:
            if owns_client:
                await client.aclose()


class HttpCmdbTopologyClient:
    """Deployment-neutral twin of InProcessCmdbTopologyClient (D3): the
    same ``lookup`` contract over HTTPS against
    ``GET /api/v1/graph/cmdb-topology``. Token hygiene mirrors
    HttpContextGraphClient — headers carry credentials, so plain HTTP is
    refused unless local development opts in.
    """

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
        if scheme != "https" and not (allow_insecure_http and scheme == "http"):
            raise ValueError(
                "HttpCmdbTopologyClient requires an https:// base_url; pass "
                "allow_insecure_http=True to use http:// in local development."
            )
        self.bearer_token = bearer_token
        self.service_token = service_token
        self.timeout = timeout
        self.client = client

    async def lookup(self, term: str) -> dict:
        headers: dict[str, str] = {}
        if self.bearer_token:
            headers["Authorization"] = f"Bearer {self.bearer_token}"
        if self.service_token:
            headers["X-Service-Token"] = self.service_token
        owns_client = self.client is None
        client = self.client or httpx.AsyncClient(timeout=self.timeout)
        try:
            response = await client.get(
                f"{self.base_url}/api/v1/graph/cmdb-topology",
                params={"ci": term},
                headers=headers,
            )
            response.raise_for_status()
            return response.json()
        finally:
            if owns_client:
                await client.aclose()
