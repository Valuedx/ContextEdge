"""From "a fix exists" to "this fix is defensible".

Roadmap E2 and E3, fused with E1. Separately they are three reports; together
they are the thing that changes what gets recommended:

    E1  how often did this actually work?
    E2  can it even apply here?
    E3  what is known to go wrong with it?

E1 alone measures efficacy that nothing acts on. This module is where the
measurement reaches the recommendation, which is the only place it changes an
outcome.

## What the substrate actually is

Both items named machinery that holds no rows. `fix_applicability_rules`,
`fix_patterns`, `fix_cohort_stats` and `case_outcome_fix_patterns` are all
empty, as are the `invalidated_fix` edges E3 was specified against. Measured
2026-08-21 on the reference corpus. What exists instead:

**Applicability** lives in the `applicability` JSON on evidence and knowledge
cases — 764 rows. Its dimensions are very unevenly populated, and that changes
what E2 can honestly claim:

    deployment        764  100%
    components        719   94%
    product_versions  148   19%
    version_floor      57    7.5%
    version_ceiling    39    5%
    environments       12    1.6%
    platforms           0    0%

So the headline use case — "structurally rule out a fix that does not match the
incident's product version" — rests on 7.5% coverage. Component and deployment
matching are strong. E2 is built for all dimensions and is honest that only two
of them currently carry weight.

**Negative knowledge** lives in `episode_steps`, which carries `failed_flag`,
`successful_flag` and `result_state` — 970 steps flagged failed, richer than
the `[did not work]` text markers E3 was specified against. Plus the
`contradicts_resolution` rows E1 now writes into the pattern ledger.

## Precedence, because the columns disagree

`result_state` and the two booleans conflict on 378 of 24,245 steps: 331 say
`result_state='failure'` with `failed_flag=false`, 33 claim
`successful_flag=true` *and* `result_state='failure'`, 14 the reverse.
`result_state` wins — it is the richer vocabulary (success / failure /
inconclusive / unknown) and the booleans cannot express `inconclusive` at all,
so a false in both is ambiguous by construction where `result_state` is not.

The 33 rows asserting both success and failure are excluded rather than
resolved. A step that claims both is not evidence of either, and picking one
would manufacture a fact from a contradiction.

## Why exclusion is conservative

An applicability verdict of `excluded` suppresses a remediation. Being wrong
about that hides a fix that would have worked, and the failure is silent — the
operator never learns the option existed. So exclusion requires BOTH sides to
state a value and for them to actually conflict. An unstated dimension never
excludes; it produces `unknown`, which is visible.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.models.episode import Episode, EpisodeStep
from contextedge.models.knowledge_case import KnowledgeCase
from contextedge.models.pattern import Pattern, PatternEvidence
from contextedge.services.efficacy_service import (
    DOCUMENTED_ONLY,
    PatternEfficacy,
    compute_pattern_efficacy,
)

logger = structlog.get_logger()

APPLICABLE = "applicable"
EXCLUDED = "excluded"
UNKNOWN_APPLICABILITY = "unknown"

RECOMMEND = "recommend"
RECOMMEND_WITH_CAUTION = "recommend_with_caution"
DO_NOT_RECOMMEND = "do_not_recommend"
INSUFFICIENT_EVIDENCE = "insufficient_evidence"

# A success rate below this, on a rate-bearing sample, downgrades a
# recommendation to caution. Distinct from the drift threshold: drift is a
# claim about documentation being wrong, this is a claim about a remediation
# being unreliable, and they should be tunable apart.
CAUTION_SUCCESS_RATE = 0.6
MIN_RATE_SAMPLE = 3

_VERSION_PART = re.compile(r"\d+")


def _version_tuple(value: str | None) -> tuple[int, ...] | None:
    """Dotted-numeric version as a comparable tuple.

    Returns None for anything it cannot read, and None never compares — an
    unparseable version must not exclude a fix, because the failure would be
    invisible.
    """
    if not value:
        return None
    parts = _VERSION_PART.findall(str(value))
    return tuple(int(p) for p in parts[:4]) if parts else None


def _pad(a: tuple[int, ...], b: tuple[int, ...]) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """8.2 vs 8.2.3 must compare as 8.2.0 vs 8.2.3, not by length."""
    n = max(len(a), len(b))
    return a + (0,) * (n - len(a)), b + (0,) * (n - len(b))


@dataclass(frozen=True)
class ApplicabilityVerdict:
    verdict: str
    reasons: tuple[str, ...] = ()
    matched_dimensions: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "reasons": list(self.reasons),
            "matched_dimensions": list(self.matched_dimensions),
        }


def assess_applicability(
    applicability: dict | None,
    context: dict | None,
) -> ApplicabilityVerdict:
    """Can this remediation apply in this context?

    Exclusion needs both sides to state a value AND to conflict. Silence on
    either side yields `unknown`, never `excluded`: suppressing a fix that
    would have worked is a failure nobody sees.
    """
    if not applicability or not context:
        return ApplicabilityVerdict(
            UNKNOWN_APPLICABILITY,
            ("no applicability stated" if not applicability else "no context given",),
        )

    reasons: list[str] = []
    matched: list[str] = []

    # --- deployment (100% populated on the reference corpus) --------------
    fix_deploy = (applicability.get("deployment") or "").strip().lower()
    ctx_deploy = (context.get("deployment") or "").strip().lower()
    if fix_deploy and ctx_deploy:
        if fix_deploy != ctx_deploy:
            return ApplicabilityVerdict(
                EXCLUDED,
                (f"fix targets {fix_deploy}; context is {ctx_deploy}",),
            )
        matched.append("deployment")

    # --- environments (1.6% populated — rarely decides anything) ----------
    fix_envs = {str(e).lower() for e in (applicability.get("environments") or [])}
    ctx_envs = {str(e).lower() for e in (context.get("environments") or [])}
    if fix_envs and ctx_envs:
        if not (fix_envs & ctx_envs):
            return ApplicabilityVerdict(
                EXCLUDED,
                (f"fix targets {sorted(fix_envs)}; context is {sorted(ctx_envs)}",),
            )
        matched.append("environments")

    # --- version floor / ceiling (7.5% / 5%) ------------------------------
    ctx_version = _version_tuple(context.get("version"))
    if ctx_version:
        for bound_key, label in (("version_floor", "below"), ("version_ceiling", "above")):
            bounds = applicability.get(bound_key) or {}
            if not isinstance(bounds, dict):
                continue
            for product, raw in bounds.items():
                bound = _version_tuple(raw)
                if bound is None:
                    continue
                left, right = _pad(ctx_version, bound)
                below = left < right
                if (label == "below" and below) or (label == "above" and left > right):
                    return ApplicabilityVerdict(
                        EXCLUDED,
                        (
                            f"context version {context.get('version')} is {label} the "
                            f"{bound_key.replace('_', ' ')} {raw} for {product}",
                        ),
                    )
                matched.append(bound_key)

    # --- components (94% populated — the dimension that usually matches) --
    fix_components = {str(c).lower() for c in (applicability.get("components") or [])}
    ctx_components = {str(c).lower() for c in (context.get("components") or [])}
    if fix_components and ctx_components:
        overlap = fix_components & ctx_components
        if overlap:
            matched.append("components")
            reasons.append(f"shares component(s): {sorted(overlap)[:3]}")
        else:
            # Deliberately NOT an exclusion. Component vocabularies are
            # LLM-extracted free text, so absence of overlap is as likely to
            # be a naming difference as a real mismatch.
            reasons.append("no component overlap (not treated as exclusion)")

    if matched:
        return ApplicabilityVerdict(APPLICABLE, tuple(reasons), tuple(sorted(set(matched))))
    return ApplicabilityVerdict(
        UNKNOWN_APPLICABILITY,
        tuple(reasons) or ("no comparable dimension stated on both sides",),
    )


def _merge_applicability(payloads: Sequence[dict]) -> dict:
    """Combine what several documented sources say about where a fix applies.

    Merged permissively, because the merged result is used to EXCLUDE. Two
    articles disagreeing about deployment is not grounds to suppress a fix in
    both, so a contested dimension becomes unstated rather than picking a
    winner. Version bounds take the loosest value seen for each product for
    the same reason: a floor that excludes is worse than one that does not.
    """
    if not payloads:
        return {}
    components: set[str] = set()
    environments: set[str] = set()
    deployments: set[str] = set()
    floors: dict[str, str] = {}
    ceilings: dict[str, str] = {}

    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        components |= {str(c).lower() for c in (payload.get("components") or [])}
        environments |= {str(e).lower() for e in (payload.get("environments") or [])}
        if (payload.get("deployment") or "").strip():
            deployments.add(payload["deployment"].strip().lower())
        for key, target, keep_lower in (
            ("version_floor", floors, True),
            ("version_ceiling", ceilings, False),
        ):
            bounds = payload.get(key) or {}
            if not isinstance(bounds, dict):
                continue
            for product, raw in bounds.items():
                existing = target.get(product)
                if existing is None:
                    target[product] = raw
                    continue
                a, b = _version_tuple(existing), _version_tuple(raw)
                if a is None or b is None:
                    continue
                left, right = _pad(a, b)
                take_new = (left > right) if keep_lower else (left < right)
                if take_new:
                    target[product] = raw

    return {
        "components": sorted(components),
        "environments": sorted(environments),
        # Contested deployment is treated as unstated. See the docstring.
        "deployment": next(iter(deployments)) if len(deployments) == 1 else "",
        "version_floor": floors,
        "version_ceiling": ceilings,
    }


async def pattern_applicability(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    pattern_ids: Sequence[uuid.UUID],
) -> dict[uuid.UUID, dict]:
    """Where each pattern's documented support says the fix applies.

    A pattern has no applicability column of its own — the statements live on
    the knowledge cases that document it, reached through the evidence ledger.
    That is the right place for them: "this applies to on-prem 8.2 and above"
    is a claim a *source* makes, and attaching it to the pattern would lose
    which source made it.

    Evidence-level applicability (629 rows on `evidence_items`) is a further
    hop through episode links and is deliberately not merged in yet: an
    incident's stated environment describes where the problem happened, not
    where the remedy applies, and conflating the two would exclude fixes on
    the strength of where they were last needed.
    """
    if not pattern_ids:
        return {}
    rows = await db.execute(
        select(PatternEvidence.pattern_id, KnowledgeCase.applicability)
        .join(
            KnowledgeCase,
            KnowledgeCase.id == PatternEvidence.evidence_object_id,
        )
        .where(
            PatternEvidence.tenant_id == tenant_id,
            PatternEvidence.pattern_id.in_(list(pattern_ids)),
            PatternEvidence.evidence_object_type == "knowledge_case",
            KnowledgeCase.applicability.is_not(None),
        )
    )
    grouped: dict[uuid.UUID, list[dict]] = {}
    for pattern_id, payload in rows.all():
        if payload:
            grouped.setdefault(pattern_id, []).append(payload)
    return {pid: _merge_applicability(payloads) for pid, payloads in grouped.items()}


@dataclass
class RemediationAdvice:
    pattern_id: uuid.UUID
    title: str | None
    efficacy: PatternEfficacy
    applicability: ApplicabilityVerdict
    known_failures: list[str] = field(default_factory=list)

    @property
    def recommendation(self) -> str:
        """The verdict, ordered so the strongest objection wins.

        Exclusion first: a fix that cannot apply is not improved by working
        elsewhere. Drift next: documented advice the record contradicts should
        not be recommended on the strength of the documentation.
        """
        if self.applicability.verdict == EXCLUDED:
            return DO_NOT_RECOMMEND
        if self.efficacy.is_drifting:
            return DO_NOT_RECOMMEND
        rate = self.efficacy.success_rate
        if rate is None:
            # No observed outcomes. Documentation alone is a starting point,
            # not a track record.
            return (
                INSUFFICIENT_EVIDENCE
                if self.efficacy.confidence_class == DOCUMENTED_ONLY
                else INSUFFICIENT_EVIDENCE
            )
        if self.efficacy.rate_base < MIN_RATE_SAMPLE:
            return INSUFFICIENT_EVIDENCE
        if rate < CAUTION_SUCCESS_RATE or self.known_failures:
            return RECOMMEND_WITH_CAUTION
        return RECOMMEND

    @property
    def rationale(self) -> str:
        rate = self.efficacy.success_rate
        if rate is None:
            observed = "no outcome-bearing episodes"
        else:
            observed = (
                f"{rate * 100:.0f}% success across {self.efficacy.rate_base} "
                f"outcome-bearing episodes"
            )
        bits = [observed, f"support: {self.efficacy.confidence_class}"]
        if self.applicability.verdict != APPLICABLE:
            bits.append(f"applicability: {self.applicability.verdict}")
        if self.known_failures:
            bits.append(f"{len(self.known_failures)} known failure(s)")
        return "; ".join(bits)

    def as_dict(self) -> dict[str, Any]:
        return {
            "pattern_id": str(self.pattern_id),
            "title": self.title,
            "recommendation": self.recommendation,
            "rationale": self.rationale,
            "efficacy": self.efficacy.as_dict(),
            "applicability": self.applicability.as_dict(),
            "known_failures": self.known_failures,
        }


async def collect_known_failures(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    pattern_ids: Sequence[uuid.UUID],
    limit_per_pattern: int = 5,
) -> dict[uuid.UUID, list[str]]:
    """What was tried under this pattern and did not work (E3).

    Reads `episode_steps.result_state`, not the boolean flags: they disagree on
    378 of 24,245 rows and `result_state` is the only one that can express
    `inconclusive`. Steps asserting both success and failure are dropped — a
    step that claims both is evidence of neither.
    """
    if not pattern_ids:
        return {}
    rows = await db.execute(
        select(
            PatternEvidence.pattern_id,
            EpisodeStep.text,
            EpisodeStep.observation,
        )
        .join(Episode, Episode.id == PatternEvidence.evidence_object_id)
        .join(EpisodeStep, EpisodeStep.episode_id == Episode.id)
        .where(
            PatternEvidence.tenant_id == tenant_id,
            PatternEvidence.pattern_id.in_(list(pattern_ids)),
            PatternEvidence.evidence_object_type == "episode",
            EpisodeStep.result_state == "failure",
            EpisodeStep.successful_flag.is_(False),
        )
    )
    out: dict[uuid.UUID, list[str]] = {}
    for pattern_id, text, observation in rows.all():
        bucket = out.setdefault(pattern_id, [])
        if len(bucket) >= limit_per_pattern:
            continue
        detail = (text or "").strip()
        if observation:
            detail = f"{detail} — {str(observation).strip()}" if detail else str(observation)
        if detail and detail not in bucket:
            bucket.append(detail[:300])
    return out


async def advise_remediations(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    context: dict | None = None,
    pattern_ids: Sequence[uuid.UUID] | None = None,
    limit: int = 20,
    classify_live: bool = False,
) -> list[RemediationAdvice]:
    """Rank remediations by whether they are defensible, not merely present.

    ``context`` is the incident's stated situation — ``deployment``,
    ``components``, ``environments``, ``version``. Omitting it yields
    `unknown` applicability throughout rather than silently applying
    everything, because "we did not check" and "it applies" are different
    claims.
    """
    # `classify_live` reads outcomes from the episodes rather than the ledger
    # column. Without it, a deployment that has never run the backfill gets
    # `insufficient_evidence` for every pattern -- which is the honest answer,
    # and useless for seeing what the backfill would give you.
    efficacy = await compute_pattern_efficacy(
        db, tenant_id, pattern_ids, classify_live=classify_live
    )
    if not efficacy:
        return []

    ids = list(efficacy)
    titles = dict(
        (
            await db.execute(select(Pattern.id, Pattern.title).where(Pattern.id.in_(ids)))
        ).all()
    )
    applicability_rows = await pattern_applicability(db, tenant_id, ids)
    failures = await collect_known_failures(db, tenant_id, ids)

    advice = [
        RemediationAdvice(
            pattern_id=pid,
            title=titles.get(pid),
            efficacy=e,
            applicability=assess_applicability(applicability_rows.get(pid), context),
            known_failures=failures.get(pid, []),
        )
        for pid, e in efficacy.items()
    ]

    order = {
        RECOMMEND: 0,
        RECOMMEND_WITH_CAUTION: 1,
        INSUFFICIENT_EVIDENCE: 2,
        DO_NOT_RECOMMEND: 3,
    }
    advice.sort(
        key=lambda a: (
            order[a.recommendation],
            -(a.efficacy.success_rate or 0.0),
            -a.efficacy.rate_base,
        )
    )
    logger.info(
        "remediation.advised",
        tenant_id=str(tenant_id),
        candidates=len(advice),
        excluded=sum(1 for a in advice if a.applicability.verdict == EXCLUDED),
        with_known_failures=sum(1 for a in advice if a.known_failures),
    )
    return advice[:limit]
