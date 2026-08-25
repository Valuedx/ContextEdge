"""Drift and freshness alerts for governed playbooks."""

from fastapi import APIRouter
from pydantic import BaseModel

from contextedge.deps import AuthUser, DbSession
from contextedge.services.drift_service import list_drift_alerts

router = APIRouter()


class DriftAlertResponse(BaseModel):
    playbook_id: str
    title: str
    issues: list[str]
    severity: str


@router.get("/alerts", response_model=list[DriftAlertResponse])
async def get_drift_alerts(db: DbSession, user: AuthUser):
    """Evaluate drift heuristics for this tenant and return playbook-level alerts.

    Read-only: does not change playbook lifecycle. Expiry transitions run on the Celery drift task.
    """
    user.require_role("knowledge_manager")
    raw = await list_drift_alerts(db, user.tenant_id)
    return [DriftAlertResponse(**item) for item in raw]
