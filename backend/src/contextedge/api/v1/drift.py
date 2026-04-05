"""Drift and freshness alerts for governed playbooks."""

from fastapi import APIRouter
from pydantic import BaseModel

from contextedge.deps import AuthUser, DbSession
from contextedge.services.drift_service import check_playbook_drift

router = APIRouter()


class DriftAlertResponse(BaseModel):
    playbook_id: str
    title: str
    issues: list[str]
    severity: str


@router.get("/alerts", response_model=list[DriftAlertResponse])
async def list_drift_alerts(db: DbSession, user: AuthUser):
    """Evaluate drift heuristics for this tenant and return playbook-level alerts.

    May transition approved playbooks to ``expired`` when past ``expiry_at``.
    """
    raw = await check_playbook_drift(db, user.tenant_id)
    return [DriftAlertResponse(**item) for item in raw]
