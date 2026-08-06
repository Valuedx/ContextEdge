"""Inventory reporting endpoint (roadmap B3).

The integration point for agent-side state: an AutomationEdge agent (or
any collector) POSTs its current inventory per CI; the diff against the
stored snapshot becomes LLM-free state-transition events linked
affects_ci to the CI — the raw material for the diagnosis-time
preceding-change window (B4).
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
    state: dict[str, str] = Field(default_factory=dict)
    observed_at: datetime | None = None


class InventoryReport(BaseModel):
    observations: list[InventoryObservation] = Field(min_length=1, max_length=200)
    source_label: str = Field(default="inventory_diff", max_length=80)


@router.post("/report")
async def report_inventory(body: InventoryReport, db: DbSession, user: AuthUser):
    results = []
    for obs in body.observations:
        counts = await observe_inventory(
            db,
            user.tenant_id,
            ci_name=obs.ci_name,
            state=obs.state,
            observed_at=obs.observed_at,
            source_label=body.source_label,
        )
        results.append({"ci_name": obs.ci_name, **counts})
    return {"observations": results}
