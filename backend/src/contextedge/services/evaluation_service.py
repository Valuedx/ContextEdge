"""Evaluation replay against the current retrieval pipeline."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.models.evaluation import EvaluationDataset, EvaluationRun
from contextedge.search.hybrid_ranker import rank_playbooks


async def execute_evaluation_run(
    db: AsyncSession,
    run_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> EvaluationRun:
    run = await db.get(EvaluationRun, run_id)
    if not run or run.tenant_id != tenant_id:
        raise ValueError("evaluation_run_not_found")

    ds = await db.get(EvaluationDataset, run.dataset_id)
    if not ds or ds.tenant_id != tenant_id:
        raise ValueError("dataset_not_found")

    now = datetime.now(timezone.utc)
    run.status = "running"
    run.started_at = now
    await db.flush()

    try:
        return await _execute_evaluation_core(db, run, ds, tenant_id, now)
    except Exception as exc:
        run.status = "failed"
        run.results = {"error": str(exc)}
        run.completed_at = datetime.now(timezone.utc)
        await db.flush()
        await db.refresh(run)
        return run


async def _execute_evaluation_core(
    db: AsyncSession,
    run: EvaluationRun,
    ds: EvaluationDataset,
    tenant_id: uuid.UUID,
    now: datetime,
) -> EvaluationRun:
    cases_out: list[dict] = []
    correct_top1 = 0
    total = 0

    for case in ds.cases or []:
        total += 1
        symptoms = case.get("symptoms") or []
        entities = case.get("entities") or []
        ctx = case.get("context") or ""
        query = " ".join(symptoms + entities + ([ctx] if ctx else []))
        expected = case.get("expected_playbook_stable_key") or case.get("expected_stable_key")

        ranked = await rank_playbooks(
            db,
            tenant_id=tenant_id,
            query_text=query,
            symptoms=symptoms,
            entities=entities,
            top_k=5,
        )
        top_stable = ranked[0].playbook.stable_key if ranked else None
        hit = bool(expected and top_stable == expected)
        if hit:
            correct_top1 += 1

        cases_out.append({
            "expected_stable_key": expected,
            "top_stable_key": top_stable,
            "top1_hit": hit,
            "top_k": [
                {"stable_key": r.playbook.stable_key, "score": r.score}
                for r in ranked[:5]
            ],
        })

    accuracy = (correct_top1 / total) if total else 0.0
    run.results = {
        "case_count": total,
        "top1_accuracy": accuracy,
        "cases": cases_out,
    }
    run.status = "completed"
    run.completed_at = datetime.now(timezone.utc)
    await db.flush()
    await db.refresh(run)
    return run

