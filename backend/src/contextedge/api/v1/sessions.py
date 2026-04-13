from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from contextedge.deps import AuthUser, DbSession
from contextedge.schemas.session import (
    DecisionTraceEventCreate,
    DecisionTraceEventResponse,
    ResolutionSessionCreate,
    ResolutionSessionResponse,
)
from contextedge.services.session_service import (
    append_trace_event,
    close_resolution_session,
    create_resolution_session,
    get_resolution_session,
    list_resolution_sessions,
)

router = APIRouter()


@router.get("", response_model=list[ResolutionSessionResponse])
async def list_sessions(
    db: DbSession,
    user: AuthUser,
    status_filter: str | None = Query(None, alias="status"),
    domain_id: UUID | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    return await list_resolution_sessions(
        db,
        tenant_id=user.tenant_id,
        status=status_filter,
        domain_id=domain_id,
        limit=limit,
        offset=offset,
    )


@router.post("", response_model=ResolutionSessionResponse, status_code=status.HTTP_201_CREATED)
async def create_session(
    body: ResolutionSessionCreate,
    db: DbSession,
    user: AuthUser,
):
    session = await create_resolution_session(
        db,
        tenant_id=user.tenant_id,
        initiated_by=user.user_id,
        symptoms=body.symptoms,
        entities=body.entities,
        external_case_ids=body.external_case_ids,
        domain_id=body.domain_id,
        notes=body.notes,
    )
    return await get_resolution_session(db, tenant_id=user.tenant_id, session_id=session.id)


@router.get("/{session_id}", response_model=ResolutionSessionResponse)
async def get_session(
    session_id: UUID,
    db: DbSession,
    user: AuthUser,
):
    session = await get_resolution_session(db, tenant_id=user.tenant_id, session_id=session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


@router.post(
    "/{session_id}/events",
    response_model=DecisionTraceEventResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_session_event(
    session_id: UUID,
    body: DecisionTraceEventCreate,
    db: DbSession,
    user: AuthUser,
):
    event = await append_trace_event(
        db,
        tenant_id=user.tenant_id,
        session_id=session_id,
        event_type=body.event_type,
        inputs=body.inputs,
        outputs=body.outputs,
        reasoning=body.reasoning,
        confidence=body.confidence,
    )
    if event is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return event


@router.patch("/{session_id}/close", response_model=ResolutionSessionResponse)
async def close_session(
    session_id: UUID,
    db: DbSession,
    user: AuthUser,
):
    session = await close_resolution_session(db, tenant_id=user.tenant_id, session_id=session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return await get_resolution_session(db, tenant_id=user.tenant_id, session_id=session_id)
