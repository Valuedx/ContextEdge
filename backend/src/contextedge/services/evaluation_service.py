"""Evaluation replay against the current retrieval pipeline."""

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.models.evaluation import EvaluationDataset, EvaluationRun
from contextedge.search.hybrid_ranker import rank_playbooks
from contextedge.services.memory_service import build_runtime_memory_context


def _as_uuid(value) -> uuid.UUID | None:
    if value is None or value == "":
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


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


def _brier_score(probs: list[float], labels: list[int]) -> float | None:
    if not probs:
        return None
    return round(
        sum((p - y) ** 2 for p, y in zip(probs, labels)) / len(probs),
        4,
    )


def _expected_calibration_error(
    probs: list[float], labels: list[int], *, bins: int = 10
) -> float | None:
    if not probs:
        return None
    bucket_total = [0] * bins
    bucket_hits = [0] * bins
    bucket_conf = [0.0] * bins
    for p, y in zip(probs, labels):
        idx = min(bins - 1, max(0, int(p * bins)))
        bucket_total[idx] += 1
        bucket_hits[idx] += y
        bucket_conf[idx] += p
    n = len(probs)
    ece = 0.0
    for total, hits, conf in zip(bucket_total, bucket_hits, bucket_conf):
        if not total:
            continue
        acc = hits / total
        mean_p = conf / total
        ece += (total / n) * abs(acc - mean_p)
    return round(ece, 4)


def _slice_metrics(rows: list[dict]) -> dict | None:
    """top1 / recall@10 / n for a subset of ranking cases."""
    n = len(rows)
    if not n:
        return None
    top1 = sum(1 for r in rows if r.get("top1_hit"))
    recall10 = sum(1 for r in rows if r.get("recall_at_10_hit"))
    return {
        "case_count": n,
        "top1_accuracy": round(top1 / n, 4),
        "recall_at_10": round(recall10 / n, 4),
    }


def _group_slice(rows: list[dict], key: str) -> dict:
    buckets: dict[str, list[dict]] = {}
    for row in rows:
        label = row.get(key) or "unknown"
        buckets.setdefault(str(label), []).append(row)
    return {name: metrics for name, group in buckets.items() if (metrics := _slice_metrics(group))}


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
    recall_at_3 = 0
    recall_at_10 = 0
    mrr_sum = 0.0
    abstain = 0
    keyword_zero = 0
    keyword_scored = 0
    calib_probs: list[float] = []
    calib_labels: list[int] = []
    apply_predicted = 0
    apply_correct = 0
    citation_cases: list[dict] = []
    prompt_version = (run.config or {}).get("episode_prompt_version")
    # Cost hardening: each citation case is a real LLM reconstruction —
    # cap per run so a huge dataset cannot burn unbounded tokens in one
    # trigger. Truncation is reported, never silent.
    max_llm_cases = int((run.config or {}).get("max_llm_cases", 100))
    llm_cases_skipped = 0
    eval_top_k = int((run.config or {}).get("top_k", 10))

    for case in ds.cases or []:
        if case.get("kind") == "episode_citation":
            if len(citation_cases) >= max_llm_cases:
                llm_cases_skipped += 1
                continue
            result = await _run_citation_case(db, tenant_id, case, prompt_version)
            citation_cases.append(result)
            cases_out.append(result)
            continue
        total += 1
        symptoms = case.get("symptoms") or []
        entities = case.get("entities") or []
        ctx = case.get("context") or ""
        expected = case.get("expected_playbook_stable_key") or case.get("expected_stable_key")
        session_id = _as_uuid(case.get("session_id"))
        domain_id = _as_uuid(case.get("domain_id"))
        max_risk_tier = case.get("max_risk_tier")
        caller_roles = case.get("caller_roles")

        memory_context = await build_runtime_memory_context(
            db,
            tenant_id=tenant_id,
            symptoms=list(symptoms),
            entities=list(entities),
            context=ctx or None,
            session_id=session_id,
            domain_id=domain_id,
            top_k=eval_top_k,
        )

        ranked = await rank_playbooks(
            db,
            tenant_id=tenant_id,
            query_text=memory_context.query_text,
            entities=list(entities),
            top_k=eval_top_k,
            domain_id=domain_id,
            max_risk_tier=max_risk_tier,
            caller_roles=list(caller_roles) if caller_roles else None,
            case_frame=memory_context.case_frame,
        )
        keys = [r.playbook.stable_key for r in ranked]
        top_stable = keys[0] if keys else None
        hit = bool(expected and top_stable == expected)
        if hit:
            correct_top1 += 1
        if not ranked:
            abstain += 1
        if ranked:
            keyword_scored += 1
            if float(ranked[0].breakdown.get("keyword") or 0.0) == 0.0:
                keyword_zero += 1
        if expected:
            if expected in keys[:3]:
                recall_at_3 += 1
            if expected in keys[:10]:
                recall_at_10 += 1
            if expected in keys:
                mrr_sum += 1.0 / (keys.index(expected) + 1)
            pred = 0.0
            if ranked:
                pred = float(
                    ranked[0].confidence_calibrated
                    if ranked[0].confidence_calibrated is not None
                    else ranked[0].confidence
                )
            calib_probs.append(pred)
            calib_labels.append(1 if hit else 0)
            if ranked and ranked[0].applicability in {"exact", "strong"}:
                apply_predicted += 1
                if hit:
                    apply_correct += 1

        cases_out.append({
            "expected_stable_key": expected,
            "top_stable_key": top_stable,
            "top1_hit": hit,
            "recall_at_10_hit": bool(expected and expected in keys[:10]),
            "source": case.get("source") or "authored",
            "generation_provenance": case.get("generation_provenance"),
            "query_text": memory_context.query_text,
            "top_k": [
                {"stable_key": r.playbook.stable_key, "score": r.score}
                for r in ranked[:eval_top_k]
            ],
        })

    accuracy = (correct_top1 / total) if total else 0.0
    results: dict = {
        "case_count": total + len(citation_cases),
        "ranking_case_count": total,
        "top1_accuracy": accuracy,
        "recall_at_3": (recall_at_3 / total) if total else 0.0,
        "recall_at_10": (recall_at_10 / total) if total else 0.0,
        "mrr": (mrr_sum / total) if total else 0.0,
        "abstain_rate": (abstain / total) if total else 0.0,
        "keyword_score_zero_rate": (
            (keyword_zero / keyword_scored) if keyword_scored else None
        ),
        "ece": _expected_calibration_error(calib_probs, calib_labels),
        "brier": _brier_score(calib_probs, calib_labels),
        "applicability_precision": (
            (apply_correct / apply_predicted) if apply_predicted else None
        ),
        "by_source": _group_slice(
            [c for c in cases_out if c.get("kind") != "episode_citation"],
            "source",
        ),
        "by_generation_provenance": _group_slice(
            [c for c in cases_out if c.get("kind") != "episode_citation"],
            "generation_provenance",
        ),
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
            "cases_skipped_by_llm_cap": llm_cases_skipped,
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

