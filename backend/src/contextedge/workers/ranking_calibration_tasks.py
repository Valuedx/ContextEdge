"""Nightly ranking calibration — writes a versioned config row, never the ranker.

Fits an isotonic map and bounded RRF arm-weight update from joinable
retrieval feedback. Below ``MIN_LABELS`` the task records a skipped row
so the audit trail exists without retuning live scores.
"""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy import func, select

from contextedge.models.evaluation import (
    RankingCalibrationConfig,
    RetrievalFeedback,
    RuntimeMatchRecord,
)
from contextedge.models.tenant import Tenant
from contextedge.search.fusion import DEFAULT_ARM_WEIGHTS
from contextedge.workers.asyncio_runner import run_async
from contextedge.workers.celery_app import celery_app

logger = structlog.get_logger()

MIN_LABELS = 120
MAX_WEIGHT_DELTA = 0.25

_POSITIVE = frozenset(
    {"confirmed", "correct_match", "selected", "validated", "helpful"}
)
_NEGATIVE = frozenset(
    {"wrong_match", "step_ineffective", "expired_workaround", "rejected"}
)


def _pava(pairs: list[tuple[float, float]]) -> list[list[float]]:
    if not pairs:
        return []
    blocks = [[x, y, 1.0] for x, y in sorted(pairs)]
    index = 0
    while index < len(blocks) - 1:
        if blocks[index][1] > blocks[index + 1][1]:
            count = blocks[index][2] + blocks[index + 1][2]
            y = (blocks[index][1] * blocks[index][2] + blocks[index + 1][1] * blocks[index + 1][2]) / count
            x = (blocks[index][0] * blocks[index][2] + blocks[index + 1][0] * blocks[index + 1][2]) / count
            blocks[index : index + 2] = [[x, y, count]]
            if index:
                index -= 1
            continue
        index += 1
    return [
        [round(block[0], 4), round(max(0.0, min(1.0, block[1])), 4)]
        for block in blocks
    ]


def _bound_weights(
    proposed: dict[str, float], previous: dict[str, float]
) -> dict[str, float]:
    bounded: dict[str, float] = {}
    for arm, default in DEFAULT_ARM_WEIGHTS.items():
        prev = float(previous.get(arm, default))
        raw = float(proposed.get(arm, prev))
        lo, hi = prev - MAX_WEIGHT_DELTA, prev + MAX_WEIGHT_DELTA
        bounded[arm] = max(0.05, min(2.0, max(lo, min(hi, raw))))
    return bounded


def _score_for_playbook(ranked_results: list, playbook_id) -> float | None:
    target = str(playbook_id)
    for row in ranked_results or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("playbook_id")) == target:
            for key in ("confidence_calibrated", "match_score", "score", "confidence"):
                value = row.get(key)
                if value is None:
                    continue
                try:
                    return float(value)
                except (TypeError, ValueError):
                    continue
    return None


@celery_app.task(name="evaluation.recalibrate_ranking")
def recalibrate_ranking(tenant_id: str = "all") -> dict:
    async def work(db):
        if tenant_id == "all":
            rows = (await db.execute(select(Tenant.id))).scalars().all()
            ids = list(rows)
        else:
            ids = [uuid.UUID(tenant_id)]
        summary: dict[str, dict] = {}
        for tid in ids:
            n = int(
                (
                    await db.execute(
                        select(func.count()).where(RetrievalFeedback.tenant_id == tid)
                    )
                ).scalar()
                or 0
            )
            previous = (
                await db.execute(
                    select(RankingCalibrationConfig)
                    .where(
                        RankingCalibrationConfig.tenant_id == tid,
                        RankingCalibrationConfig.status == "active",
                    )
                    .order_by(RankingCalibrationConfig.created_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            prev_weights = dict(DEFAULT_ARM_WEIGHTS)
            if previous and isinstance(previous.arm_weights, dict):
                prev_weights.update(
                    {str(k): float(v) for k, v in previous.arm_weights.items()}
                )
            next_version = int(previous.version) + 1 if previous else 1
            if n < MIN_LABELS:
                logger.info(
                    "ranking.calibration_skipped",
                    tenant_id=str(tid),
                    labels=n,
                    required=MIN_LABELS,
                )
                db.add(
                    RankingCalibrationConfig(
                        tenant_id=tid,
                        version=next_version,
                        status="skipped",
                        arm_weights=prev_weights,
                        isotonic_points=(
                            previous.isotonic_points if previous else []
                        ),
                        labels_used=n,
                        notes=f"insufficient labels ({n} < {MIN_LABELS})",
                    )
                )
                summary[str(tid)] = {"labels": n, "fitted": False}
                continue

            labelled = (
                await db.execute(
                    select(RetrievalFeedback, RuntimeMatchRecord)
                    .join(
                        RuntimeMatchRecord,
                        (RuntimeMatchRecord.tenant_id == RetrievalFeedback.tenant_id)
                        & (RuntimeMatchRecord.match_id == RetrievalFeedback.match_id),
                    )
                    .where(
                        RetrievalFeedback.tenant_id == tid,
                        RetrievalFeedback.match_id.is_not(None),
                        RetrievalFeedback.playbook_id.is_not(None),
                    )
                )
            ).all()
            pairs: list[tuple[float, float]] = []
            arm_hits: dict[str, float] = {arm: 0.0 for arm in DEFAULT_ARM_WEIGHTS}
            arm_n = 0
            for feedback, match in labelled:
                label = None
                if feedback.feedback_type in _POSITIVE:
                    label = 1.0
                elif feedback.feedback_type in _NEGATIVE:
                    label = 0.0
                if label is None:
                    continue
                score = _score_for_playbook(match.ranked_results, feedback.playbook_id)
                if score is None:
                    continue
                pairs.append((score, label))
                if label >= 1.0:
                    arm_n += 1
                    breakdown = {}
                    for row in match.ranked_results or []:
                        if (
                            isinstance(row, dict)
                            and str(row.get("playbook_id")) == str(feedback.playbook_id)
                        ):
                            breakdown = row.get("scoring_breakdown") or {}
                            break
                    if breakdown.get("semantic"):
                        arm_hits["r1_embedding"] += 1
                    if breakdown.get("keyword"):
                        arm_hits["r2_lexical"] += 1
                    if breakdown.get("precedent"):
                        arm_hits["r3_signature"] += 1
                    if breakdown.get("quality"):
                        arm_hits["r4_evidence"] += 1

            if len(pairs) < MIN_LABELS:
                logger.info(
                    "ranking.calibration_skipped",
                    tenant_id=str(tid),
                    labels=n,
                    joinable=len(pairs),
                    required=MIN_LABELS,
                )
                db.add(
                    RankingCalibrationConfig(
                        tenant_id=tid,
                        version=next_version,
                        status="skipped",
                        arm_weights=prev_weights,
                        isotonic_points=(
                            previous.isotonic_points if previous else []
                        ),
                        labels_used=len(pairs),
                        notes=(
                            f"insufficient joinable labels "
                            f"({len(pairs)} < {MIN_LABELS})"
                        ),
                    )
                )
                summary[str(tid)] = {
                    "labels": n,
                    "joinable": len(pairs),
                    "fitted": False,
                }
                continue

            proposed = dict(prev_weights)
            if arm_n:
                for arm in DEFAULT_ARM_WEIGHTS:
                    proposed[arm] = 0.5 + arm_hits[arm] / arm_n
            weights = _bound_weights(proposed, prev_weights)
            points = _pava(pairs)
            db.add(
                RankingCalibrationConfig(
                    tenant_id=tid,
                    version=next_version,
                    status="active",
                    arm_weights=weights,
                    isotonic_points=points,
                    labels_used=len(pairs),
                    notes="isotonic + bounded RRF weights",
                )
            )
            logger.info(
                "ranking.calibration_fitted",
                tenant_id=str(tid),
                labels=len(pairs),
                version=next_version,
            )
            summary[str(tid)] = {
                "labels": len(pairs),
                "fitted": True,
                "version": next_version,
            }
        await db.commit()
        return {"by_tenant": summary}

    return run_async(work)
