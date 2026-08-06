"""Does capping the adjudicator's thinking budget change its decisions?

Identity adjudication is the largest single cost line in the pipeline —
22% of all tokens, and **91% of its output is reasoning** (779 of 851
tokens per message, to produce a verdict and a confidence). Capping
thinking is therefore the biggest available saving.

It is also the one place where capping was already observed to do harm:
the verdict stayed the same at every budget while the *confidence* moved
0.95 -> 0.80, and ``AUTO_LINK_THRESHOLDS["person"]`` is 0.95. A cap there
silently converts auto-links into review-queue items — the model still
gets the answer right and the pipeline stops believing it.

So the question this measures is NOT "does confidence drop". It does.
The question is whether the drop preserves ORDERING, because a threshold
only cares about rank:

- If capped confidence is a monotone shift of uncapped confidence, then
  every case keeps its relative position, and the fix is to re-tune the
  threshold to the new scale. The saving is free.
- If capping reshuffles which cases score highest, no threshold recovers
  the old behaviour, and the cap must not ship at any price.

Cases carry ground truth so accuracy is measured, not assumed:

- ``match``      — a real alias variant of an identity that is in the
                   candidate list. The adjudicator should match it.
- ``distinct``   — a numbered sibling ("mailgw01" vs "mailgw02"). These
                   are DIFFERENT hosts, they trigram-match strongly, and
                   the prompt names the case explicitly because getting
                   it wrong merges two servers into one.

Run:
    python -m contextedge.evals.adjudication_thinking_eval --samples 3
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import uuid
from dataclasses import dataclass, field
from typing import Any

TENANT = uuid.UUID("00000000-0000-0000-0000-000000000001")

# Budgets to compare. ``None`` means "leave the provider's dynamic
# default alone" — the behaviour shipping today.
DEFAULT_BUDGETS: tuple[int | None, ...] = (None, 0, 128, 512)


@dataclass
class Case:
    """One adjudication with a known right answer."""

    label: str
    kind: str  # "match" | "distinct"
    incoming: dict
    candidates: list[dict]
    # For "match", the candidate id that should win. For "distinct",
    # no candidate should win.
    expected_candidate_id: str | None = None

    def is_correct(self, decision: str, candidate_id: str | None) -> bool:
        if self.kind == "match":
            return decision == "match" and candidate_id == self.expected_candidate_id
        # A numbered sibling must NOT be merged. Abstaining is acceptable
        # — it costs a reviewer's time, not a corrupted graph.
        return decision in ("new_identity", "needs_review")


@dataclass
class Observation:
    case: Case
    budget: int | None
    decision: str
    candidate_id: str | None
    confidence: float
    correct: bool


@dataclass
class VariantResult:
    budget: int | None
    observations: list[Observation] = field(default_factory=list)

    @property
    def accuracy(self) -> float:
        if not self.observations:
            return 0.0
        return sum(o.correct for o in self.observations) / len(self.observations)

    @property
    def mean_confidence(self) -> float:
        if not self.observations:
            return 0.0
        return statistics.fmean(o.confidence for o in self.observations)

    def auto_link_rate(self, threshold: float) -> float:
        """Share of correct matches the pipeline would actually act on.

        This is the number that matters. A verdict the graph does not
        believe is worth nothing: it lands in the review queue and waits
        for a human.
        """
        matches = [o for o in self.observations if o.case.kind == "match"]
        if not matches:
            return 0.0
        acted = [o for o in matches if o.correct and o.confidence >= threshold]
        return len(acted) / len(matches)

    def confidence_by_case(self) -> dict[str, float]:
        """Mean confidence per case label, for the ordering comparison."""
        buckets: dict[str, list[float]] = {}
        for o in self.observations:
            buckets.setdefault(o.case.label, []).append(o.confidence)
        return {label: statistics.fmean(vals) for label, vals in buckets.items()}


def rank_agreement(a: dict[str, float], b: dict[str, float]) -> float | None:
    """Spearman rank correlation between two case->confidence maps.

    The decisive statistic. If capping merely shifts every confidence
    down by a similar amount, rank is preserved and a re-tuned threshold
    reproduces today's behaviour exactly. If rank breaks, no threshold
    does.
    """
    shared = sorted(set(a) & set(b))
    n = len(shared)
    if n < 3:
        return None

    def ranks(values: list[float]) -> list[float]:
        order = sorted(range(len(values)), key=lambda i: values[i])
        out = [0.0] * len(values)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
                j += 1
            shared_rank = (i + j) / 2 + 1
            for k in range(i, j + 1):
                out[order[k]] = shared_rank
            i = j + 1
        return out

    ra = ranks([a[label] for label in shared])
    rb = ranks([b[label] for label in shared])
    mean_a, mean_b = statistics.fmean(ra), statistics.fmean(rb)
    num = sum((x - mean_a) * (y - mean_b) for x, y in zip(ra, rb, strict=True))
    den_a = sum((x - mean_a) ** 2 for x in ra) ** 0.5
    den_b = sum((y - mean_b) ** 2 for y in rb) ** 0.5
    if den_a == 0 or den_b == 0:
        return None
    return num / (den_a * den_b)


async def build_cases(limit: int = 12) -> list[Case]:
    """Draw cases from the live graph so the inputs are real ones."""
    from sqlalchemy import text

    from contextedge.database import async_session_factory

    cases: list[Case] = []
    async with async_session_factory() as db:
        # Identities that actually carry an alias distinct from their
        # canonical name — those are the real "is this the same thing"
        # decisions the adjudicator faces.
        rows = (
            await db.execute(
                text(
                    """
                    select ci.id, ci.canonical_name, ci.entity_type,
                           array_agg(distinct ia.alias_text) as aliases
                    from canonical_identities ci
                    join identity_aliases ia on ia.canonical_identity_id = ci.id
                    where ci.tenant_id = :t
                      and lower(ia.alias_text) <> lower(ci.canonical_name)
                    group by ci.id, ci.canonical_name, ci.entity_type
                    limit :n
                    """
                ),
                {"t": str(TENANT), "n": limit},
            )
        ).all()

        for row in rows:
            aliases = [a for a in (row.aliases or []) if a]
            if not aliases:
                continue
            # Distractors: other identities of the same type, so the
            # adjudicator has to discriminate rather than accept the only
            # option on offer.
            others = (
                await db.execute(
                    text(
                        """
                        select ci.id, ci.canonical_name
                        from canonical_identities ci
                        where ci.tenant_id = :t and ci.entity_type = :et
                          and ci.id <> :self limit 3
                        """
                    ),
                    {"t": str(TENANT), "et": row.entity_type, "self": str(row.id)},
                )
            ).all()

            candidates = [
                {
                    "id": str(row.id),
                    "name": row.canonical_name,
                    "aliases": aliases[:10],
                    "resolution_state": "resolved",
                }
            ] + [
                {
                    "id": str(o.id),
                    "name": o.canonical_name,
                    "aliases": [],
                    "resolution_state": "resolved",
                }
                for o in others
            ]

            cases.append(
                Case(
                    label=f"match:{row.canonical_name[:28]}",
                    kind="match",
                    incoming={
                        "entity_type": row.entity_type,
                        "name": aliases[0],
                        "identifiers": {},
                        "context": "",
                    },
                    candidates=candidates,
                    expected_candidate_id=str(row.id),
                )
            )

    # Numbered siblings are synthetic on purpose: they are the failure
    # the prompt was rewritten for, and the live graph may hold none.
    for base, suffix_a, suffix_b in (
        ("mailgw", "01", "02"),
        ("ae-app-prod-", "1", "2"),
        ("qa-agent-", "3", "4"),
    ):
        cases.append(
            Case(
                label=f"distinct:{base}{suffix_b}",
                kind="distinct",
                incoming={
                    "entity_type": "device",
                    "name": f"{base}{suffix_b}",
                    "identifiers": {},
                    "context": "",
                },
                candidates=[
                    {
                        "id": str(uuid.uuid4()),
                        "name": f"{base}{suffix_a}",
                        "aliases": [f"{base}{suffix_a}"],
                        "resolution_state": "resolved",
                    }
                ],
            )
        )
    return cases


async def adjudicate_once(case: Case, budget: int | None) -> tuple[str, str | None, float]:
    """One adjudication at one thinking budget."""
    import json

    from contextedge.ai.prompts import get_prompt
    from contextedge.ai.provider import llm_complete_json_validated
    from contextedge.config import settings
    from contextedge.services.identity_service import AdjudicationResult

    prompt = get_prompt("identity_adjudication", TENANT)
    original = dict(getattr(settings, "llm_thinking_budgets", {}) or {})
    patched = dict(original)
    if budget is None:
        patched.pop("identity_adjudication", None)
    else:
        patched["identity_adjudication"] = budget
    settings.llm_thinking_budgets = patched
    try:
        result = await llm_complete_json_validated(
            prompt.format_user(
                incoming=json.dumps(case.incoming, ensure_ascii=False),
                candidates=json.dumps(case.candidates, ensure_ascii=False),
            ),
            AdjudicationResult,
            task="classification",
            system_prompt=prompt.system,
            tenant_id=TENANT,
            prompt_name=prompt.name,
            prompt_version=prompt.version,
        )
    finally:
        settings.llm_thinking_budgets = original
    return result.decision, result.candidate_id, float(result.confidence)


async def run(budgets: tuple[int | None, ...], samples: int, limit: int) -> None:
    from contextedge.services.identity_service import (
        AUTO_LINK_THRESHOLDS,
        DEFAULT_AUTO_LINK_THRESHOLD,
    )

    cases = await build_cases(limit=limit)
    print(f"{len(cases)} cases ({sum(c.kind == 'match' for c in cases)} match, "
          f"{sum(c.kind == 'distinct' for c in cases)} distinct) x {samples} samples\n")

    threshold = AUTO_LINK_THRESHOLDS.get("person", DEFAULT_AUTO_LINK_THRESHOLD)
    results: list[VariantResult] = []
    for budget in budgets:
        variant = VariantResult(budget=budget)
        for case in cases:
            for _ in range(samples):
                try:
                    decision, candidate_id, confidence = await adjudicate_once(case, budget)
                except Exception as exc:  # noqa: BLE001
                    print(f"  ({type(exc).__name__} on {case.label} @ {budget})")
                    continue
                variant.observations.append(
                    Observation(
                        case=case,
                        budget=budget,
                        decision=decision,
                        candidate_id=candidate_id,
                        confidence=confidence,
                        correct=case.is_correct(decision, candidate_id),
                    )
                )
        results.append(variant)
        label = "dynamic" if budget is None else str(budget)
        print(
            f"  budget {label:>8}: accuracy {variant.accuracy:>5.0%}  "
            f"mean confidence {variant.mean_confidence:.2f}  "
            f"auto-link@{threshold} {variant.auto_link_rate(threshold):>5.0%}",
            flush=True,
        )

    baseline = results[0]
    base_conf = baseline.confidence_by_case()
    print(f"\n{'budget':>10} {'accuracy':>9} {'mean conf':>10} "
          f"{'auto-link':>10} {'rank agree':>11}")
    for variant in results:
        label = "dynamic" if variant.budget is None else str(variant.budget)
        rho = rank_agreement(base_conf, variant.confidence_by_case())
        rho_text = "  baseline" if variant is baseline else (
            f"{rho:>11.2f}" if rho is not None else "          -"
        )
        print(
            f"{label:>10} {variant.accuracy:>9.0%} {variant.mean_confidence:>10.2f} "
            f"{variant.auto_link_rate(threshold):>10.0%} {rho_text}"
        )

    print(
        "\nRank agreement is the decision. Near 1.0 means capping only "
        "rescales confidence,\nso re-tuning the threshold recovers today's "
        "behaviour and the saving is free.\nA low value means capping "
        "reshuffles which cases look certain, and no threshold\nrecovers it."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=3)
    parser.add_argument("--limit", type=int, default=12)
    parser.add_argument(
        "--budgets",
        type=str,
        default="",
        help="Comma-separated, e.g. '0,128'. Empty uses the defaults.",
    )
    args = parser.parse_args()

    budgets: tuple[Any, ...] = DEFAULT_BUDGETS
    if args.budgets.strip():
        budgets = (None,) + tuple(int(b) for b in args.budgets.split(","))

    asyncio.run(run(budgets, samples=args.samples, limit=args.limit))


if __name__ == "__main__":
    main()
