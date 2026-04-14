from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from contextedge.deps import AuthUser, DbSession
from contextedge.models.source import SyncRun
from contextedge.schemas.source import SyncRunResponse

router = APIRouter()


@router.get("", response_model=list[SyncRunResponse])
async def list_all_sync_runs(
    db: DbSession,
    user: AuthUser,
    status_filter: str | None = None,
    run_type: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    q = select(SyncRun).where(SyncRun.tenant_id == user.tenant_id)
    if status_filter:
        q = q.where(SyncRun.status == status_filter)
    if run_type:
        q = q.where(SyncRun.run_type == run_type)
    q = q.order_by(SyncRun.created_at.desc()).limit(limit).offset(offset)
    result = await db.execute(q)
    return result.scalars().all()


@router.get("/{run_id}", response_model=SyncRunResponse)
async def get_sync_run(run_id: UUID, db: DbSession, user: AuthUser):
    result = await db.execute(
        select(SyncRun).where(SyncRun.id == run_id, SyncRun.tenant_id == user.tenant_id)
    )
    run = result.scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail="Sync run not found")
    return run


@router.post("/{run_id}/retry", status_code=202)
async def retry_sync_run(run_id: UUID, db: DbSession, user: AuthUser):
    user.require_role("domain_admin")
    result = await db.execute(
        select(SyncRun).where(SyncRun.id == run_id, SyncRun.tenant_id == user.tenant_id)
    )
    run = result.scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail="Sync run not found")
    if run.status not in ("failed", "dead_letter"):
        raise HTTPException(status_code=400, detail="Only failed runs can be retried")
    if run.source_object_id is None:
        raise HTTPException(status_code=400, detail="Run has no source object to retry")

    from contextedge.workers.sync_tasks import run_backfill, run_incremental_sync
    if run.run_type == "backfill":
        run_backfill.delay(str(run.source_id), str(run.source_object_id), str(user.tenant_id))
    else:
        run_incremental_sync.delay(str(run.source_id), str(run.source_object_id), str(user.tenant_id))
    return {"status": "retry_queued", "dispatched_as": run.run_type or "incremental"}
