from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from contextedge.deps import AuthUser, DbSession
from contextedge.models.evidence import EvidenceItem, Thread
from contextedge.schemas.common import TaskDispatchResponse
from contextedge.schemas.evidence import EvidenceItemResponse, ThreadResponse

router = APIRouter()


@router.get("", response_model=list[ThreadResponse])
async def list_threads(
    db: DbSession,
    user: AuthUser,
    source_id: UUID | None = None,
    hydration_status: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    q = select(Thread).where(Thread.tenant_id == user.tenant_id)
    if source_id:
        q = q.where(Thread.source_id == source_id)
    if hydration_status:
        q = q.where(Thread.hydration_status == hydration_status)
    q = q.order_by(Thread.last_message_at.desc().nullslast()).limit(limit).offset(offset)
    result = await db.execute(q)
    return result.scalars().all()


@router.get("/{thread_id}", response_model=ThreadResponse)
async def get_thread(thread_id: UUID, db: DbSession, user: AuthUser):
    from sqlalchemy import func
    result = await db.execute(
        select(Thread).where(Thread.id == thread_id, Thread.tenant_id == user.tenant_id)
    )
    thread = result.scalar_one_or_none()
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")

    count_res = await db.execute(
        select(func.count()).select_from(EvidenceItem).where(
            EvidenceItem.thread_id == thread_id,
            EvidenceItem.tenant_id == user.tenant_id,
        )
    )
    actual_count = count_res.scalar_one()
    if actual_count > 0:
        thread.message_count = actual_count
        # Do not infer hydration completion from a non-zero count. The parent
        # ticket may already be linked to this thread before any comments are
        # hydrated. Only the hydration worker can assert that the connector
        # returned the complete conversation.

    return thread


@router.get("/{thread_id}/evidence", response_model=list[EvidenceItemResponse])
async def get_thread_evidence(thread_id: UUID, db: DbSession, user: AuthUser):
    result = await db.execute(
        select(EvidenceItem).where(
            EvidenceItem.thread_id == thread_id,
            EvidenceItem.tenant_id == user.tenant_id,
        ).order_by(EvidenceItem.created_at_source)
    )
    return result.scalars().all()


@router.post(
    "/{thread_id}/hydrate",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=TaskDispatchResponse,
)
async def trigger_thread_hydration(
    thread_id: UUID,
    db: DbSession,
    user: AuthUser,
):
    result = await db.execute(
        select(Thread).where(Thread.id == thread_id, Thread.tenant_id == user.tenant_id)
    )
    thread = result.scalar_one_or_none()
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")

    from contextedge.workers.hydration_tasks import hydrate_thread

    thread.hydration_status = "queued"
    await db.flush()
    task = hydrate_thread.delay(
        thread.external_thread_id,
        str(thread.source_id),
        str(user.tenant_id),
    )
    return TaskDispatchResponse(
        status="hydration_queued",
        task_id=task.id,
        detail={"thread_id": str(thread.id)},
    )
