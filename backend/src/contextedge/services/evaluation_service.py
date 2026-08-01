"""Evaluation replay against the current retrieval pipeline."""

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.models.evaluation import EvaluationDataset, EvaluationRun
from contextedge.search.hybrid_ranker import rank_playbooks


def evaluation_run_to_dict(run: EvaluationRun) -> dict:
    """JSON-serializable snapshot for Celery task results."""
    return {
        "id": str(run.id),
        "tenant_id": str(run.tenant_id),
        "dataset_id": str(run.dataset_id),
        "status": run.status,
        "config": run.config or {},
        "results": run.results,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
    }


async def execute_evaluation_run(
    db: AsyncSession,
    run_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> dict:
    run = await db.get(EvaluationRun, run_id)
    if not run or run.tenant_id != tenant_id:
        raise ValueError("evaluation_run_not_found")

    ds = await db.get(EvaluationDataset, run.dataset_id)
    if not ds or ds.tenant_id != tenant_id:
        raise ValueError("dataset_not_found")

    now = datetime.now(UTC)
    run.status = "running"
    run.started_at = now
    await db.flush()

    try:
        await _execute_evaluation_core(db, run, ds, tenant_id, now)
        await db.refresh(run)
        return evaluation_run_to_dict(run)
    except Exception as exc:
        run.status = "failed"
        run.results = {"error": str(exc)}
        run.completed_at = datetime.now(UTC)
        await db.flush()
        await db.refresh(run)
        return evaluation_run_to_dict(run)


async def _run_citation_case(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    case: dict,
    prompt_version: str | None,
) -> dict:
    """C5: per-step gold citations vs the model's. Two rates:

    - unsupported_step_rate — predicted steps citing nothing;
    - wrong_attribution_rate — compared steps (by order, up to the
      shorter of predicted/gold) whose citations are not a subset of
      that step's gold set.
    """
    from contextedge.ai.extractors.episode_extractor import reconstruct_episode

    evidence_items = case.get("evidence_items") or []
    gold = [set(refs) for refs in (case.get("gold_step_citations") or [])]
    episodes = await reconstruct_episode(
        evidence_items,
        tenant_id=tenant_id,
        db=db,
        prompt_version=prompt_version,
    )
    steps = episodes[0].get("steps", []) if episodes else []
    unsupported = sum(1 for s in steps if not s.get("evidence_refs"))
    compared = min(len(steps), len(gold))
    wrong = 0
    for i in range(compared):
        refs = set(steps[i].get("evidence_refs") or [])
        if refs and gold[i] and not refs.issubset(gold[i]):
            wrong += 1
    return {
        "kind": "episode_citation",
        "steps_predicted": len(steps),
        "steps_gold": len(gold),
        "unsupported_steps": unsupported,
        "unsupported_step_rate": round(unsupported / len(steps), 3) if steps else None,
        "compared_steps": compared,
        "wrong_attribution": wrong,
        "wrong_attribution_rate": round(wrong / compared, 3) if compared else None,
    }


async def _execute_evaluation_core(
    db: AsyncSession,
    run: EvaluationRun,
    ds: EvaluationDataset,
    tenant_id: uuid.UUID,
    now: datetime,
) -> None:
    cases_out: list[dict] = []
    correct_top1 = 0
    total = 0
    citation_cases: list[dict] = []
    prompt_version = (run.config or {}).get("episode_prompt_version")

    for case in ds.cases or []:
        if case.get("kind") == "episode_citation":
            result = await _run_citation_case(db, tenant_id, case, prompt_version)
            citation_cases.append(result)
            cases_out.append(result)
            continue
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
    results: dict = {
        "case_count": total + len(citation_cases),
        "top1_accuracy": accuracy,
        "cases": cases_out,
    }
    if citation_cases:
        rated_unsupported = [
            c["unsupported_step_rate"]
            for c in citation_cases
            if c["unsupported_step_rate"] is not None
        ]
        rated_wrong = [
            c["wrong_attribution_rate"]
            for c in citation_cases
            if c["wrong_attribution_rate"] is not None
        ]
        results["citation"] = {
            "case_count": len(citation_cases),
            "episode_prompt_version": prompt_version or "default",
            "mean_unsupported_step_rate": (
                round(sum(rated_unsupported) / len(rated_unsupported), 3)
                if rated_unsupported
                else None
            ),
            "mean_wrong_attribution_rate": (
                round(sum(rated_wrong) / len(rated_wrong), 3)
                if rated_wrong
                else None
            ),
        }
    run.results = results
    run.status = "completed"
    run.completed_at = datetime.now(UTC)
    await db.flush()
    await db.refresh(run)

