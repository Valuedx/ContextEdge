"""Versioned ranking calibration — isotonic map + RRF arm weights.

The ranker never retunes itself. Nightly ``evaluation.recalibrate_ranking``
writes a ``ranking_calibration_configs`` row; this module only reads the
latest ``active`` row for the tenant. Until a fit exists, calibrated
confidence is a monotone pass-through of the fused score.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.search.fusion import DEFAULT_ARM_WEIGHTS


@dataclass(frozen=True)
class CalibrationMap:
    arm_weights: dict[str, float] = field(default_factory=dict)
    isotonic_points: tuple[tuple[float, float], ...] = ()


def _piecewise(score: float, points: tuple[tuple[float, float], ...]) -> float:
    if not points:
        return score
    if score <= points[0][0]:
        return max(0.0, min(1.0, points[0][1]))
    if score >= points[-1][0]:
        return max(0.0, min(1.0, points[-1][1]))
    for index in range(1, len(points)):
        x0, y0 = points[index - 1]
        x1, y1 = points[index]
        if score <= x1:
            span = (x1 - x0) or 1e-9
            mapped = y0 + (score - x0) / span * (y1 - y0)
            return max(0.0, min(1.0, mapped))
    return score


def calibrate_confidence(score: float, mapping: CalibrationMap | None = None) -> float:
    clamped = max(0.0, min(1.0, float(score)))
    if mapping is None or not mapping.isotonic_points:
        return clamped
    return _piecewise(clamped, mapping.isotonic_points)


async def load_active_calibration(
    db: AsyncSession, tenant_id: UUID
) -> CalibrationMap | None:
    import inspect

    try:
        from contextedge.models.evaluation import RankingCalibrationConfig
    except Exception:
        return None
    try:
        result = await db.execute(
            select(RankingCalibrationConfig)
            .where(
                RankingCalibrationConfig.tenant_id == tenant_id,
                RankingCalibrationConfig.status == "active",
            )
            .order_by(RankingCalibrationConfig.created_at.desc())
            .limit(1)
        )
        row = result.scalar_one_or_none()
        if inspect.isawaitable(row):
            row = await row
    except Exception:
        return None
    if row is None:
        return None
    raw_weights = getattr(row, "arm_weights", None)
    if not isinstance(raw_weights, dict):
        return None
    weights = dict(DEFAULT_ARM_WEIGHTS)
    for arm, value in raw_weights.items():
        try:
            weights[str(arm)] = float(value)
        except (TypeError, ValueError):
            continue
    points: list[tuple[float, float]] = []
    raw_points = getattr(row, "isotonic_points", None)
    if isinstance(raw_points, list):
        for item in raw_points:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                try:
                    points.append((float(item[0]), float(item[1])))
                except (TypeError, ValueError):
                    continue
    points.sort(key=lambda pair: pair[0])
    return CalibrationMap(arm_weights=weights, isotonic_points=tuple(points))
