"""Inventory reporting endpoint (roadmap B3).

The integration point for agent-side state: an AutomationEdge agent (or
any collector) POSTs its current inventory per CI; the diff against the
stored snapshot becomes LLM-free state-transition events linked
affects_ci to the CI — the raw material for the diagnosis-time
preceding-change window (B4).

Role-gated to ``knowledge_manager``: inventory reports WRITE topology
(state events, snapshots, optionally new CI entities), and topology is
governed knowledge. Automated collectors authenticate with a service
token whose ``roles`` include it (see SERVICE_TOKENS_JSON).
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel, Field

from contextedge.deps import AuthUser, DbSession
from contextedge.services.inventory_diff_service import observe_inventory

router = APIRouter()


class InventoryObservation(BaseModel):
    ci_name: str = Field(min_length=1, max_length=255)
    # The collector's own record identity. When given, resolution is
    # exact instead of by display name — two CIs may share a name.
    external_system: str | None = Field(default=None, max_length=100)
    external_id: str | None = Field(default=None, max_length=500)
    state: dict[str, str] = Field(default_factory=dict)
    observed_at: datetime | None = None


class InventoryReport(BaseModel):
    observations: list[InventoryObservation] = Field(min_length=1, max_length=200)
    source_label: str = Field(default="inventory_diff", max_length=80)
    # Unknown CI names are refused (status "unknown_ci") unless the
    # report explicitly opts into creating them — a typo in a collector
    # config must not quietly grow the topology.
    create_missing: bool = False


@router.post("/report")
async def report_inventory(body: InventoryReport, db: DbSession, user: AuthUser):
    user.require_role("knowledge_manager")
    results = []
    for obs in body.observations:
        counts = await observe_inventory(
            db,
            user.tenant_id,
            ci_name=obs.ci_name,
            state=obs.state,
            observed_at=obs.observed_at,
            source_label=body.source_label,
            external_system=obs.external_system,
            external_id=obs.external_id,
            create_missing=body.create_missing,
        )
        results.append({"ci_name": obs.ci_name, **counts})
    return {"observations": results}
