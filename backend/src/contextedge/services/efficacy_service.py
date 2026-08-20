"""Efficacy: did this fix actually work, and does the documentation still hold?

Roadmap E1, and the capability the competitive landscape does not have. Three
independent research paths in August 2026 found no vendor — ServiceNow,
incident.io, PagerDuty, Rootly, Glean — that verifiably tracks whether a
remediation *worked*. Everyone tracks two adjacent things and conflates them
with this one:

    activity   was the action item closed?          (common)
    relevance  did the user like the answer?        (common, sophisticated)
    efficacy   did the fix prevent recurrence?      (essentially absent)

ServiceNow's Article Health Score is the sharpest illustration: it grades every
knowledge article 0-100 on image alt tags (17%), multiple H1 tags (16%), bad
links (17%), article length (17%), title relevancy (17%) and readability (16%).
An article recommending a restart that fails four times in five scores 100.

## What this computes

`PatternEvidence` already records what each piece of evidence contributes and on
what epistemic footing — `evidence_class` of empirical / documented /
prescriptive, with a CHECK constraint that only an episode may be empirical and
only empirical rows carry an outcome. That ledger is the substrate. What was
missing is that `outcome` was NULL on every row, because episode outcomes are
free text in ~9,000 phrasings. `outcome_classification` normalizes them; this
module aggregates the result.

Two things become computable that a bare `episode_count` cannot support:

**Confidence class.** A pattern supported by three KB articles and a pattern
supported by nineteen resolved incidents are not the same pattern.
`DOCUMENTED_ONLY` says so out loud, and a pattern *graduates* to `EMPIRICAL`
as incidents confirm it — which is what makes cold start survivable.

**Knowledge drift.** A documented resolution accumulating failures while the
article remains approved upstream is a stale KB, and nothing else in the system
would notice. This is the query that produces "KB-108 recommends a restart;
observed 19% success across 27 recent incidents".

## Rate arithmetic, stated because it is easy to get quietly wrong

The denominator is success + partial + failure. `unknown` is **excluded**, not
counted as failure: an unclassifiable corpus would otherwise drive every rate
toward zero and read as fixes that stopped working. A pattern with no
rate-bearing outcomes has a success rate of `None`, not 0.0 — the difference
between "we do not know" and "it never works", which is exactly the distinction
the coverage work exists to protect.

Partial counts in the denominator but not the numerator: restoring service with
a workaround is not the same as fixing the cause, and a pattern whose successes
are mostly workarounds should not read as fully effective.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.models.episode import Episode
from contextedge.models.pattern import Pattern, PatternEvidence
from contextedge.services.outcome_classification import (
    FAILURE,
    PARTIAL,
    SUCCESS,
    UNKNOWN,
    classify_outcome,
    counts_toward_rate,
    support_role_for,
)

logger = structlog.get_logger()

# Confidence classes. A pattern's footing, not its score.
DOCUMENTED_ONLY = "DOCUMENTED_ONLY"
EMPIRICAL = "EMPIRICAL"
MIXED = "MIXED"
UNSUPPORTED = "UNSUPPORTED"

# Below this success rate, with at least MIN_DRIFT_SAMPLE rate-bearing
# outcomes, a documented pattern is flagged as drifting. Both numbers are
# deliberately conservative: a drift claim tells someone their documentation
# is wrong, and being wrong about that is expensive. Untuned — there is no
# labelled drift set to tune against yet, and inventing one would make the
# threshold look measured when it is chosen.
DRIFT_SUCCESS_RATE = 0.5
MIN_DRIFT_SAMPLE = 5


@dataclass(frozen=True)
class PatternEfficacy:
    pattern_id: uuid.UUID
    success: int = 0
    partial: int = 0
    failure: int = 0
    unknown: int = 0
    documented_support: int = 0
    prescriptive_support: int = 0
    last_observed_at: datetime | None = None

    @property
    def rate_base(self) -> int:
        return self.success + self.partial + self.failure

    @property
    def success_rate(self) -> float | None:
        """None, never 0.0, when nothing is rate-bearing.

        "We have no outcome data" and "it never works" are different claims
        and must not share a representation.
        """
        return (self.success / self.rate_base) if self.rate_base else None

    @property
    def empirical_support(self) -> int:
        return self.success + self.partial + self.failure + self.unknown

    @property
    def confidence_class(self) -> str:
        has_doc = bool(self.documented_support or self.prescriptive_support)
        has_emp = bool(self.empirical_support)
        if has_doc and has_emp:
            return MIXED
        if has_doc:
            return DOCUMENTED_ONLY
        if has_emp:
            return EMPIRICAL
        return UNSUPPORTED

    @property
    def is_drifting(self) -> bool:
        """Documented advice that the record contradicts.

        Requires documentation to drift FROM: a purely empirical pattern with
        a low success rate is a hard problem, not stale knowledge, and calling
        it drift would send someone to edit an article that does not exist.
        """
        if not (self.documented_support or self.prescriptive_support):
            return False
        if self.rate_base < MIN_DRIFT_SAMPLE:
            return False
        rate = self.success_rate
        return rate is not None and rate < DRIFT_SUCCESS_RATE

    def as_dict(self) -> dict[str, Any]:
        return {
            "pattern_id": str(self.pattern_id),
            "success": self.success,
            "partial": self.partial,
            "failure": self.failure,
            "unknown": self.unknown,
            "documented_support": self.documented_support,
            "prescriptive_support": self.prescriptive_support,
            "empirical_support": self.empirical_support,
            "success_rate": self.success_rate,
            "confidence_class": self.confidence_class,
            "is_drifting": self.is_drifting,
            "last_observed_at": (
                self.last_observed_at.isoformat() if self.last_observed_at else None
            ),
        }


async def backfill_ledger_outcomes(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    dry_run: bool = True,
    limit: int | None = None,
) -> dict[str, int]:
    """Classify episode outcomes into `pattern_evidence.outcome`.

    Only empirical rows are touched — the CHECK constraint enforces that only
    an episode may carry an outcome, and this respects it rather than
    discovering it at execution time.

    ``dry_run`` defaults to True: this rewrites a column that downstream
    ranking will read, and a rule change should be measured on the real corpus
    before it moves any number.
    """
    stmt = (
        select(PatternEvidence.id, Episode.final_outcome, Episode.created_at)
        .join(Episode, Episode.id == PatternEvidence.evidence_object_id)
        .where(
            PatternEvidence.tenant_id == tenant_id,
            PatternEvidence.evidence_class == "empirical",
            PatternEvidence.evidence_object_type == "episode",
        )
    )
    if limit:
        stmt = stmt.limit(limit)

    counts: dict[str, int] = {SUCCESS: 0, PARTIAL: 0, FAILURE: 0, UNKNOWN: 0}
    updates: list[tuple[uuid.UUID, str]] = []
    for row_id, final_outcome, _created in (await db.execute(stmt)).all():
        outcome = classify_outcome(final_outcome)
        counts[outcome] += 1
        updates.append((row_id, outcome))

    if not dry_run:
        # Grouped by outcome: the corpus has 1,416 empirical rows and four
        # possible values, so this is four statements rather than 1,416
        # round-trips.
        by_outcome: dict[str, list[uuid.UUID]] = {}
        for row_id, outcome in updates:
            by_outcome.setdefault(outcome, []).append(row_id)
        for outcome, ids in by_outcome.items():
            await db.execute(
                update(PatternEvidence)
                .where(PatternEvidence.id.in_(ids))
                .values(outcome=outcome, support_role=support_role_for(outcome))
            )

    result = {**counts, "rows": len(updates), "written": 0 if dry_run else len(updates)}
    logger.info("efficacy.ledger_backfill", tenant_id=str(tenant_id), **result)
    return result


async def compute_pattern_efficacy(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    pattern_ids: Sequence[uuid.UUID] | None = None,
    *,
    classify_live: bool = False,
) -> dict[uuid.UUID, PatternEfficacy]:
    """Roll the ledger up per pattern.

    Computed on read rather than stored. The inputs change whenever an episode
    is reconstructed or an article is attached, and a stored rollup that drifts
    from its ledger is worse than one that costs a query — it would be wrong in
    exactly the direction that looks like a finding.

    ``classify_live`` classifies each episode's outcome text on the fly instead
    of reading the stored column. Two uses: previewing what a classifier rule
    change would do to every rollup before any row is rewritten, and reading a
    corpus whose ledger has never been backfilled. It is slower and it is the
    only way to answer the question without writing.
    """
    # The third column is either the stored label or the episode's raw text,
    # and the caller's mode decides which. Outer join so a ledger row whose
    # object is not an episode (documented, prescriptive) still comes back.
    if classify_live:
        stmt = (
            select(
                PatternEvidence.pattern_id,
                PatternEvidence.evidence_class,
                Episode.final_outcome,
                PatternEvidence.observed_at,
            )
            .outerjoin(Episode, Episode.id == PatternEvidence.evidence_object_id)
            .where(PatternEvidence.tenant_id == tenant_id)
        )
    else:
        stmt = select(
            PatternEvidence.pattern_id,
            PatternEvidence.evidence_class,
            PatternEvidence.outcome,
            PatternEvidence.observed_at,
        ).where(PatternEvidence.tenant_id == tenant_id)
    if pattern_ids:
        stmt = stmt.where(PatternEvidence.pattern_id.in_(list(pattern_ids)))

    acc: dict[uuid.UUID, dict[str, Any]] = {}
    for pattern_id, evidence_class, outcome, observed_at in (
        await db.execute(stmt)
    ).all():
        bucket = acc.setdefault(
            pattern_id,
            {
                SUCCESS: 0,
                PARTIAL: 0,
                FAILURE: 0,
                UNKNOWN: 0,
                "documented": 0,
                "prescriptive": 0,
                "last": None,
            },
        )
        if evidence_class == "empirical":
            # In live mode the third column is the episode's free text, not a
            # stored label, so it goes through the classifier here.
            resolved = classify_outcome(outcome) if classify_live else (outcome or UNKNOWN)
            # A stored value outside the vocabulary folds into `unknown`
            # rather than growing a key nobody reads: PatternEfficacy takes
            # only the four it knows, so an unfolded stray would vanish from
            # every count, quietly moving the rate with nothing to show for it.
            if resolved not in (SUCCESS, PARTIAL, FAILURE, UNKNOWN):
                resolved = UNKNOWN
            bucket[resolved] += 1
            if observed_at and (
                bucket["last"] is None or observed_at > bucket["last"]
            ):
                bucket["last"] = observed_at
        elif evidence_class == "documented":
            bucket["documented"] += 1
        elif evidence_class == "prescriptive":
            bucket["prescriptive"] += 1

    return {
        pattern_id: PatternEfficacy(
            pattern_id=pattern_id,
            success=b[SUCCESS],
            partial=b[PARTIAL],
            failure=b[FAILURE],
            unknown=b[UNKNOWN],
            documented_support=b["documented"],
            prescriptive_support=b["prescriptive"],
            last_observed_at=b["last"],
        )
        for pattern_id, b in acc.items()
    }


async def find_drifting_knowledge(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Documented resolutions the record contradicts.

    The headline query: patterns carrying documented support whose observed
    outcomes fall below the drift threshold. Sorted worst-first, because a
    reviewer's attention is the scarce resource and the article failing four
    times in five should be the one they see.
    """
    efficacy = await compute_pattern_efficacy(db, tenant_id)
    drifting = [e for e in efficacy.values() if e.is_drifting]
    if not drifting:
        return []

    drifting.sort(key=lambda e: (e.success_rate or 0.0, -e.rate_base))
    drifting = drifting[:limit]

    titles = dict(
        (
            await db.execute(
                select(Pattern.id, Pattern.title).where(
                    Pattern.id.in_([e.pattern_id for e in drifting])
                )
            )
        ).all()
    )
    return [
        {
            **e.as_dict(),
            "pattern_title": titles.get(e.pattern_id),
            "finding": (
                f"{e.documented_support + e.prescriptive_support} source(s) document "
                f"this resolution; observed "
                f"{(e.success_rate or 0.0) * 100:.0f}% success across "
                f"{e.rate_base} outcome-bearing episodes "
                f"({e.failure} failed, {e.partial} workaround only)."
            ),
        }
        for e in drifting
    ]


async def counts_by_confidence_class(
    db: AsyncSession, tenant_id: uuid.UUID
) -> dict[str, int]:
    """How the pattern estate is supported. The cold-start picture."""
    efficacy = await compute_pattern_efficacy(db, tenant_id)
    out: dict[str, int] = {}
    for e in efficacy.values():
        out[e.confidence_class] = out.get(e.confidence_class, 0) + 1
    return out


__all__ = [
    "DOCUMENTED_ONLY",
    "EMPIRICAL",
    "MIXED",
    "UNSUPPORTED",
    "PatternEfficacy",
    "backfill_ledger_outcomes",
    "compute_pattern_efficacy",
    "counts_by_confidence_class",
    "counts_toward_rate",
    "find_drifting_knowledge",
]
