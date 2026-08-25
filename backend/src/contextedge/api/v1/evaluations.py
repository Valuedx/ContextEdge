from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from contextedge.deps import AuthUser, DbSession
from contextedge.models.evaluation import EvaluationDataset, EvaluationRun

router = APIRouter()


class EvalDatasetCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    cases: list = Field(default_factory=list)


class EvalDatasetResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    name: str
    description: str | None
    cases: list
    created_at: datetime
    updated_at: datetime
    model_config = {"from_attributes": True}


class EvalRunCreate(BaseModel):
    dataset_id: UUID
    config: dict = Field(default_factory=dict)


class EvalRunResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    dataset_id: UUID
    config: dict
    status: str
    results: dict | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime
    model_config = {"from_attributes": True}


@router.get("/datasets", response_model=list[EvalDatasetResponse])
async def list_datasets(db: DbSession, user: AuthUser, limit: int = Query(50, ge=1, le=200)):
    user.require_role("knowledge_manager")
    result = await db.execute(
        select(EvaluationDataset)
        .where(EvaluationDataset.tenant_id == user.tenant_id)
        .limit(limit)
    )
    return result.scalars().all()


@router.post("/datasets", response_model=EvalDatasetResponse, status_code=status.HTTP_201_CREATED)
async def create_dataset(body: EvalDatasetCreate, db: DbSession, user: AuthUser):
    user.require_role("knowledge_manager")
    ds = EvaluationDataset(
        tenant_id=user.tenant_id,
        name=body.name,
        description=body.description,
        cases=body.cases,
    )
    db.add(ds)
    await db.flush()
    await db.refresh(ds)
    return ds


@router.get("/runs", response_model=list[EvalRunResponse])
async def list_runs(db: DbSession, user: AuthUser, limit: int = Query(50, ge=1, le=200)):
    user.require_role("knowledge_manager")
    result = await db.execute(
        select(EvaluationRun)
        .where(EvaluationRun.tenant_id == user.tenant_id)
        .order_by(EvaluationRun.created_at.desc())
        .limit(limit)
    )
    return result.scalars().all()


@router.post("/runs", response_model=EvalRunResponse, status_code=status.HTTP_201_CREATED)
async def create_run(body: EvalRunCreate, db: DbSession, user: AuthUser):
    user.require_role("knowledge_manager")
    dataset = (
        await db.execute(
            select(EvaluationDataset).where(
                EvaluationDataset.id == body.dataset_id,
                EvaluationDataset.tenant_id == user.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if dataset is None:
        raise HTTPException(status_code=404, detail="Evaluation dataset not found")
    run = EvaluationRun(
        tenant_id=user.tenant_id,
        dataset_id=body.dataset_id,
        config=body.config,
        status="pending",
    )
    db.add(run)
    await db.flush()
    await db.refresh(run)

    from contextedge.workers.evaluation_tasks import run_evaluation
    run_evaluation.delay(str(run.id), str(user.tenant_id))

    return run


@router.get("/runs/{run_id}", response_model=EvalRunResponse)
async def get_run(run_id: UUID, db: DbSession, user: AuthUser):
    user.require_role("knowledge_manager")
    result = await db.execute(
        select(EvaluationRun).where(
            EvaluationRun.id == run_id,
            EvaluationRun.tenant_id == user.tenant_id,
        )
    )
    run = result.scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail="Evaluation run not found")
    return run
