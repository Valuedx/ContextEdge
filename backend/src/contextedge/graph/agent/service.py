"""Orchestration service and authorization-scope construction."""

from __future__ import annotations

from uuid import UUID

import structlog
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.deps import CurrentUser
from contextedge.graph.agent.contracts import (
    AgentGraphAccessScope,
    AgentGraphRequest,
    AgentGraphSubset,
)
from contextedge.graph.agent.profiles import get_projection_profile
from contextedge.graph.agent.repository import SQLAlchemyAgentGraphRepository
from contextedge.graph.agent.selector import AgentGraphSelector
from contextedge.models.tenant import Domain
from contextedge.search.risk_policy import effective_max_risk_tier
from contextedge.services.event_log_service import append_operational_event

logger = structlog.get_logger(__name__)


def _risk_cap(user: CurrentUser) -> str | None:
    """Same cap as ``/runtime/match`` (``effective_max_risk_tier``)."""
    return effective_max_risk_tier(user)


async def build_agent_graph_scope(
    db: AsyncSession,
    user: CurrentUser,
    requested_domain_id: UUID | None,
) -> AgentGraphAccessScope:
    if requested_domain_id is not None:
        domain = (
            await db.execute(
                select(Domain).where(
                    Domain.id == requested_domain_id,
                    Domain.tenant_id == user.tenant_id,
                    Domain.is_active.is_(True),
                )
            )
        ).scalar_one_or_none()
        if domain is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Domain not found",
            )
        if (
            user.principal_type == "service_account"
            and user.allowed_domain_ids is not None
            and requested_domain_id not in user.allowed_domain_ids
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Service account is not allowed to access this domain",
            )
        if (
            user.workspace_ids
            and domain.workspace_id is not None
            and domain.workspace_id not in user.workspace_ids
            and not user.has_role("tenant_admin")
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Domain workspace is not visible to this principal",
            )

    allowed_domains = (
        tuple(user.allowed_domain_ids)
        if user.principal_type == "service_account"
        and user.allowed_domain_ids is not None
        else None
    )
    return AgentGraphAccessScope(
        tenant_id=user.tenant_id,
        principal_id=user.user_id,
        principal_type=user.principal_type,
        roles=tuple(user.roles),
        workspace_ids=tuple(user.workspace_ids or []),
        domain_id=requested_domain_id,
        allowed_domain_ids=allowed_domains,
        playbook_risk_cap=_risk_cap(user),
    )


class AgentGraphProjectionService:
    def __init__(
        self,
        db: AsyncSession,
        *,
        selector: AgentGraphSelector | None = None,
    ):
        self.db = db
        self.repository = SQLAlchemyAgentGraphRepository(db)
        self.selector = selector or AgentGraphSelector()

    async def project(
        self,
        request: AgentGraphRequest,
        scope: AgentGraphAccessScope,
        *,
        invocation_mode: str = "api",
        record_event: bool = True,
    ) -> AgentGraphSubset:
        try:
            profile = get_projection_profile(request.profile)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=str(exc),
            ) from exc

        effective_request = request
        if request.domain_id != scope.domain_id:
            effective_request = request.model_copy(update={"domain_id": scope.domain_id})
        subset = await self.selector.select(
            self.repository,
            effective_request,
            scope,
            profile,
        )
        logger.info(
            "agent_graph_projection",
            projection_id=str(subset.projection_id),
            tenant_id=str(scope.tenant_id),
            principal_type=scope.principal_type,
            profile=subset.profile,
            schema_version=subset.schema_version,
            seed_count=len(subset.seeds),
            node_count=subset.usage.nodes,
            relationship_count=subset.usage.relationships,
            truncated=subset.truncated,
            invocation_mode=invocation_mode,
        )
        if record_event:
            await append_operational_event(
                self.db,
                tenant_id=scope.tenant_id,
                entity_type="agent_graph_projection",
                entity_id=subset.projection_id,
                session_id=request.session_id,
                actor_id=scope.principal_id,
                event_type="agent_graph.projected",
                payload={
                    "profile": subset.profile,
                    "schema_version": subset.schema_version,
                    "seed_count": len(subset.seeds),
                    "node_count": subset.usage.nodes,
                    "relationship_count": subset.usage.relationships,
                    "characters": subset.usage.characters,
                    "truncated": subset.truncated,
                    "truncation_reasons": subset.truncation_reasons,
                    "invocation_mode": invocation_mode,
                },
            )
        return subset
