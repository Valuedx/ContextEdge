"""Relational-to-graph relationship materialization and reconciliation."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.graph.builder import ensure_edge
from contextedge.models.action_policy import ActionPolicy, DecisionActionPolicy
from contextedge.models.case_outcome import CaseOutcome, CaseOutcomeFixPattern
from contextedge.models.claim import Claim, ClaimEvidence, DecisionClaim, DecisionEvidence
from contextedge.models.decision import Decision
from contextedge.models.error_signature import ErrorSignature, FixPattern
from contextedge.models.execution import ApprovalRequest, ExecutionRun
from contextedge.models.session import ResolutionSession


@dataclass(slots=True)
class ReconciliationResult:
    tenant_id: UUID
    relationships_seen: int = 0


class GraphRelationshipMaterializer:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def _scalars(
        self,
        statement: Any,
        batch_size: int,
    ) -> AsyncIterator[Any]:
        stream = await self.db.stream_scalars(
            statement.execution_options(yield_per=batch_size)
        )
        async for row in stream:
            yield row

    async def _rows(
        self,
        statement: Any,
        batch_size: int,
    ) -> AsyncIterator[Any]:
        stream = await self.db.stream(statement.execution_options(yield_per=batch_size))
        async for row in stream:
            yield row

    async def _edge(
        self,
        tenant_id: UUID,
        source_type: str,
        source_id: UUID,
        target_type: str,
        target_id: UUID | None,
        edge_type: str,
        *,
        domain_id: UUID | None = None,
        weight: float = 1.0,
        metadata: dict | None = None,
    ) -> bool:
        if target_id is None:
            return False
        await ensure_edge(
            self.db,
            tenant_id,
            source_type,
            source_id,
            target_type,
            target_id,
            edge_type,
            domain_id=domain_id,
            weight=weight,
            metadata=metadata,
        )
        return True

    async def reconcile_tenant(
        self,
        tenant_id: UUID,
        *,
        batch_size: int = 500,
    ) -> ReconciliationResult:
        result = ReconciliationResult(tenant_id=tenant_id)

        async for row in self._scalars(
            select(ResolutionSession).where(
                ResolutionSession.tenant_id == tenant_id
            ),
            batch_size,
        ):
            for node_id, edge_type in (
                (row.user_entity_id, "involves_user"),
                (row.workflow_entity_id, "targets_workflow"),
                (row.request_entity_id, "tracks_request"),
                (row.agent_entity_id, "runs_on_agent"),
            ):
                result.relationships_seen += await self._edge(
                    tenant_id,
                    "session",
                    row.id,
                    "entity",
                    node_id,
                    edge_type,
                    domain_id=row.domain_id,
                )

        async for row in self._scalars(
            select(ExecutionRun).where(ExecutionRun.tenant_id == tenant_id),
            batch_size,
        ):
            if row.session_id is not None:
                result.relationships_seen += await self._edge(
                    tenant_id,
                    "session",
                    row.session_id,
                    "execution_run",
                    row.id,
                    "has_execution",
                )
            result.relationships_seen += await self._edge(
                tenant_id,
                "execution_run",
                row.id,
                "playbook",
                row.playbook_id,
                "executes",
            )

        async for row in self._scalars(
            select(ApprovalRequest).where(ApprovalRequest.tenant_id == tenant_id),
            batch_size,
        ):
            result.relationships_seen += await self._edge(
                tenant_id,
                "execution_run",
                row.execution_run_id,
                "approval_request",
                row.id,
                "requires_approval",
            )

        async for row in self._scalars(
            select(Claim).where(Claim.tenant_id == tenant_id),
            batch_size,
        ):
            result.relationships_seen += await self._edge(
                tenant_id,
                "claim",
                row.id,
                "session",
                row.case_id,
                "asserted_in",
                domain_id=row.domain_id,
            )
            result.relationships_seen += await self._edge(
                tenant_id,
                "claim",
                row.id,
                "claim",
                row.superseded_by_claim_id,
                "superseded_by",
                domain_id=row.domain_id,
            )

        support_edges = {
            "supports": "supported_by",
            "contradicts": "contradicted_by",
            "weakens": "weakened_by",
            "weakened_by": "weakened_by",
        }
        async for link, domain_id in self._rows(
            select(ClaimEvidence, Claim.domain_id)
            .join(Claim, Claim.id == ClaimEvidence.claim_id)
            .where(Claim.tenant_id == tenant_id),
            batch_size,
        ):
            result.relationships_seen += await self._edge(
                tenant_id,
                "claim",
                link.claim_id,
                "evidence",
                link.evidence_id,
                support_edges.get(link.support_type, "supported_by"),
                domain_id=domain_id,
                weight=float(link.weight),
            )

        async for link in self._scalars(
            select(DecisionEvidence)
            .join(Decision, Decision.id == DecisionEvidence.decision_id)
            .where(Decision.tenant_id == tenant_id),
            batch_size,
        ):
            result.relationships_seen += await self._edge(
                tenant_id,
                "decision",
                link.decision_id,
                "evidence",
                link.evidence_id,
                "based_on",
            )

        async for link, domain_id in self._rows(
            select(DecisionClaim, Claim.domain_id)
            .join(Claim, Claim.id == DecisionClaim.claim_id)
            .where(DecisionClaim.tenant_id == tenant_id),
            batch_size,
        ):
            result.relationships_seen += await self._edge(
                tenant_id,
                "decision",
                link.decision_id,
                "claim",
                link.claim_id,
                "supported_by_claim",
                domain_id=domain_id,
                weight=float(link.weight),
                metadata={"use_type": link.use_type},
            )

        async for link in self._scalars(
            select(DecisionActionPolicy).where(
                DecisionActionPolicy.tenant_id == tenant_id
            ),
            batch_size,
        ):
            result.relationships_seen += await self._edge(
                tenant_id,
                "decision",
                link.decision_id,
                "action_policy",
                link.action_policy_id,
                "applied_policy",
            )

        async for row in self._scalars(
            select(ActionPolicy).where(ActionPolicy.tenant_id == tenant_id),
            batch_size,
        ):
            result.relationships_seen += await self._edge(
                tenant_id,
                "action_policy",
                row.id,
                "entity",
                row.workflow_entity_id,
                "governs",
                domain_id=row.domain_id,
            )

        async for row in self._scalars(
            select(ErrorSignature).where(ErrorSignature.tenant_id == tenant_id),
            batch_size,
        ):
            result.relationships_seen += await self._edge(
                tenant_id,
                "error_signature",
                row.id,
                "pattern",
                row.pattern_id,
                "aggregated_by",
                domain_id=row.domain_id,
            )

        async for row in self._scalars(
            select(FixPattern).where(FixPattern.tenant_id == tenant_id),
            batch_size,
        ):
            for target_type, target_id, edge_type in (
                ("error_signature", row.error_signature_id, "addresses"),
                ("entity", row.workflow_entity_id, "applies_to"),
                ("playbook", row.recommended_playbook_id, "recommends"),
            ):
                result.relationships_seen += await self._edge(
                    tenant_id,
                    "fix_pattern",
                    row.id,
                    target_type,
                    target_id,
                    edge_type,
                    domain_id=row.domain_id,
                )

        async for row in self._scalars(
            select(CaseOutcome).where(CaseOutcome.tenant_id == tenant_id),
            batch_size,
        ):
            result.relationships_seen += await self._edge(
                tenant_id,
                "session",
                row.case_id,
                "case_outcome",
                row.id,
                "resulted_in",
            )

        async for link in self._scalars(
            select(CaseOutcomeFixPattern).where(
                CaseOutcomeFixPattern.tenant_id == tenant_id
            ),
            batch_size,
        ):
            result.relationships_seen += await self._edge(
                tenant_id,
                "case_outcome",
                link.case_outcome_id,
                "fix_pattern",
                link.fix_pattern_id,
                "invalidated_fix" if link.result == "failed" else "validated_fix",
                weight=float(link.confidence or 1.0),
                metadata={"result": link.result},
            )

        return result
