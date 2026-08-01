"""Compute baseline / delta signals for evidence items.

Populates `EvidenceItem.baseline_ref` and `delta_signal` so the reviewer
console's Zone 4 can render "was 74% a week ago" comparisons instead of
bare current values.

This service runs a **generic relationship-only baseline**: it finds the
most recent prior evidence with the same tenant + evidence_type +
source_object_id within a time window, and records "last seen N days ago"
alongside the prior evidence id. Connectors that ingest rich time-series
(Intune disk-free %, CrowdStrike threat signals) should populate
`baseline_ref` directly at ingest with numeric prior / current / delta
values — the JSONB shape is open-ended by design so richer baselines
from connector code and relationship baselines from this worker
coexist cleanly.

`delta_signal` defaults to `"neutral"` when a baseline is established;
the worker leaves richer severity heuristics (amber/red) to connectors
that have domain knowledge of what a meaningful delta looks like.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.models.evidence import EvidenceItem

DEFAULT_WINDOW_DAYS = 7

# Valid values for EvidenceItem.delta_signal. Neutral = baseline observed,
# no notable change. Amber = warrants attention. Red = critical delta.
DELTA_SIGNALS = ("neutral", "amber", "red")

# Evidence types we don't bother baselining — free-text KB articles and
# ticket bodies don't carry time-series semantics the generic worker can
# usefully compare. Connectors that do have semantics (telemetry streams)
# should still receive a baseline; opt them in by keeping them out of this
# skip list.
_SKIP_EVIDENCE_TYPES: set[str] = set()


async def compute_evidence_baseline(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    evidence_id: uuid.UUID,
    window_days: int = DEFAULT_WINDOW_DAYS,
) -> dict | None:
    """Compute and persist a baseline for one evidence item.

    Returns the `baseline_ref` dict that was written (or None on skip) so
    callers can log or chain follow-up work.
    """
    target = await db.get(EvidenceItem, evidence_id)
    if target is None or target.tenant_id != tenant_id:
        return None

    # Can't baseline without a source_object_id — the generic worker needs
    # a stable dedup key to match prior observations against. Leave the
    # field null; connectors with richer semantics can still populate it.
    if target.source_object_id is None:
        return None
    if target.evidence_type in _SKIP_EVIDENCE_TYPES:
        return None

    current_ingested_at = target.ingested_at or datetime.now(UTC)
    window_start = current_ingested_at - timedelta(days=window_days)

    prior_stmt = (
        select(EvidenceItem)
        .where(
            EvidenceItem.tenant_id == tenant_id,
            EvidenceItem.id != evidence_id,
            EvidenceItem.evidence_type == target.evidence_type,
            EvidenceItem.source_object_id == target.source_object_id,
            EvidenceItem.ingested_at >= window_start,
            EvidenceItem.ingested_at < current_ingested_at,
        )
        .order_by(EvidenceItem.ingested_at.desc())
        .limit(1)
    )
    prior = (await db.execute(prior_stmt)).scalar_one_or_none()

    if prior is None:
        baseline_ref = {
            "window_days": window_days,
            "first_seen_in_window": True,
            "comparison_label": f"first observation in {window_days}d window",
            "source": "relationship_worker",
        }
    else:
        delta = current_ingested_at - prior.ingested_at
        days_since = max(0, int(delta.total_seconds() // 86400))
        hours_since = int(delta.total_seconds() // 3600)
        if days_since >= 1:
            label = f"last seen {days_since} day{'s' if days_since != 1 else ''} ago"
        elif hours_since >= 1:
            label = f"last seen {hours_since} hour{'s' if hours_since != 1 else ''} ago"
        else:
            label = "last seen under an hour ago"

        baseline_ref = {
            "window_days": window_days,
            "first_seen_in_window": False,
            "prior_evidence_id": str(prior.id),
            "prior_ingested_at": prior.ingested_at.isoformat() if prior.ingested_at else None,
            "days_since_prior": days_since,
            "hours_since_prior": hours_since,
            "comparison_label": label,
            "source": "relationship_worker",
        }

    target.baseline_ref = baseline_ref
    # Only stamp neutral if we established a baseline and the evidence
    # doesn't already carry a richer signal from an ingest-time writer.
    if target.delta_signal is None:
        target.delta_signal = "neutral"

    await db.flush()
    return baseline_ref
