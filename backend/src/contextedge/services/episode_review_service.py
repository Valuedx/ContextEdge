"""AI first-pass review of pending episode drafts (EPISODE_AI_REVIEW).

The reviewer's place in the pipeline: reconstruction writes drafts,
dedup consolidates them, and — when the operator has opted in — this
stage reads each pending draft against its own evidence and either
annotates it for the human queue (``advisory``) or additionally
approves the subset that clears BOTH the model's verdict AND the
deterministic floors below (``auto_approve``). Off by default; a
pipeline that quietly grades its own homework is not a feature.

The floors exist because the model's "approve" is necessary, never
sufficient (the classifier-proposes / policy-disposes convention):

- ``MIN_EVIDENCE``: a single-source account has no corroboration to be
  judged against; grounding of a one-message story is vacuous.
- ``MIN_OUTCOME_CHARS``: resolution is what downstream learns from; an
  episode without a substantive outcome gains nothing from approval.
- ``MIN_VERDICT_CONFIDENCE``: below it, the model itself is unsure —
  exactly the case a human exists for.

An auto-approval is permanently distinguishable from a human one:
``reviewer_user_id`` stays NULL and ``episodes.ai_review`` records the
verdict, floors, prompt version, and timestamp.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import structlog
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.models.episode import Episode
from contextedge.models.evidence import EvidenceItem

logger = structlog.get_logger()

REVIEW_MODES = ("off", "advisory", "auto_approve")

MIN_EVIDENCE = 2
MIN_OUTCOME_CHARS = 20
MIN_VERDICT_CONFIDENCE = 0.8

# Bounded context: enough evidence for grounding judgement, never the
# whole thread. Selection is CITATION-DRIVEN: the first sweep sent a
# blind head+tail sample, and the reviewer held 100/100 drafts with
# "steps not supported by the provided evidence excerpts" — structurally
# true, since most steps' cited evidence was never in the window. The
# excerpts now cover what the steps cite, so a grounding verdict judges
# the episode instead of the sampling.
MAX_EVIDENCE_ITEMS = 10
MAX_EVIDENCE_CHARS = 450


def review_priority_expression():
    """Deterministic review-priority score, computed in SQL.

    Transparent additive factors (the change-risk convention — the
    weights ARE the explanation): +40 substantive final outcome, +20
    substantive root cause, +3 per evidence item capped at 10, +10 x
    extraction_confidence (the weakest factor because it is the only
    self-reported one). Shared by the review-queue API ordering and the
    AI-review sweep, so the machine reviews in the same order a human
    would have.
    """
    evidence_count = case(
        (
            func.jsonb_typeof(Episode.evidence_ids) == "array",
            func.jsonb_array_length(Episode.evidence_ids),
        ),
        else_=0,
    )
    return (
        case(
            (func.length(func.coalesce(Episode.final_outcome, "")) >= 20, 40),
            else_=0,
        )
        + case(
            (func.length(func.coalesce(Episode.root_cause_summary, "")) >= 20, 20),
            else_=0,
        )
        + func.least(func.coalesce(evidence_count, 0), 10) * 3
        + func.coalesce(Episode.extraction_confidence, 0.0) * 10
    )


def passes_auto_approve_floors(episode: Episode, verdict: dict) -> tuple[bool, list[str]]:
    """The deterministic half of the gate. Returns (passes, failed_floors)."""
    failed: list[str] = []
    evidence_ids = episode.evidence_ids if isinstance(episode.evidence_ids, list) else []
    if len(evidence_ids) < MIN_EVIDENCE:
        failed.append(f"evidence<{MIN_EVIDENCE}")
    if len((episode.final_outcome or "").strip()) < MIN_OUTCOME_CHARS:
        failed.append("no_substantive_outcome")
    if verdict.get("verdict") != "approve":
        failed.append("verdict_not_approve")
    if float(verdict.get("confidence") or 0.0) < MIN_VERDICT_CONFIDENCE:
        failed.append(f"confidence<{MIN_VERDICT_CONFIDENCE}")
    return (not failed, failed)


async def _evidence_excerpts(
    db: AsyncSession, episode: Episode, steps: list | None = None
) -> str:
    """Citation-driven excerpts: show the reviewer what the steps cite.

    Priority order for the item budget:
    1. Evidence the steps actually cite (``evidence_refs``) — a grounding
       check against excerpts that omit the cited evidence can only
       produce false "unsupported" verdicts, which is exactly what the
       first sweep did (100/100 held on grounding).
    2. The chronologically LAST item — the fix confirmation lives at the
       thread's end and check #3 (does the outcome follow?) needs it.
    3. The chronologically FIRST item — the original complaint.
    Remaining budget fills with uncited items, oldest first.
    """
    ids: list[uuid.UUID] = []
    for raw in episode.evidence_ids or []:
        try:
            ids.append(uuid.UUID(str(raw)))
        except ValueError:
            continue
    if not ids:
        return ""
    cited: set[str] = set()
    for step in steps or []:
        for ref in step.evidence_refs or []:
            cited.add(str(ref))

    rows = (
        await db.execute(
            select(EvidenceItem)
            .where(
                EvidenceItem.tenant_id == episode.tenant_id,
                EvidenceItem.id.in_(tuple(ids)),
            )
            .order_by(EvidenceItem.created_at_source.asc().nulls_last())
        )
    ).scalars().all()
    if not rows:
        return ""

    chosen: list = []
    chosen_ids: set = set()

    def take(item) -> None:
        if item.id not in chosen_ids and len(chosen) < MAX_EVIDENCE_ITEMS:
            chosen.append(item)
            chosen_ids.add(item.id)

    # Cited first, then the ends, then chronological fill.
    for item in rows:
        if str(item.id) in cited:
            take(item)
    take(rows[-1])
    take(rows[0])
    for item in rows:
        take(item)

    chosen.sort(key=lambda i: (i.created_at_source is None, i.created_at_source))
    parts = []
    for item in chosen:
        body = (item.body_summary or item.body_text or "").strip()
        marker = " [cited]" if str(item.id) in cited else ""
        parts.append(
            f"[{item.evidence_type}]{marker} {item.title or '(untitled)'}\n"
            f"{body[:MAX_EVIDENCE_CHARS]}"
        )
    return "\n\n".join(parts)


async def ai_review_episode(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    episode: Episode,
    *,
    mode: str,
) -> dict:
    """Review one pending draft; annotate it; approve it only when BOTH
    halves of the gate agree and the mode allows. Returns a small result
    dict for the sweep's tallies."""
    from contextedge.ai.classifiers.episode_review import review_episode_llm
    from contextedge.models.episode import EpisodeStep

    # Explicit query, never episode.steps: the lazy relationship raises
    # MissingGreenlet under async (the B3 lesson, re-learned once already).
    steps = (
        await db.execute(
            select(EpisodeStep)
            .where(EpisodeStep.episode_id == episode.id)
            .order_by(EpisodeStep.step_order)
        )
    ).scalars().all()
    steps_text = "\n".join(
        f"{step.step_order}. [{step.step_type}] {(step.text or '')[:200]}"
        for step in steps
    )
    # The actual conflicting claims, not a bare count — "2 recorded" gave
    # the reviewer nothing to judge honesty-of-representation against.
    contradiction_lines = []
    for contradiction in (episode.contradictions or [])[:2]:
        if isinstance(contradiction, dict):
            topic = str(contradiction.get("topic", "?"))[:80]
            claims = " vs ".join(
                str(account.get("claim", ""))[:120]
                for account in (contradiction.get("accounts") or [])[:2]
                if isinstance(account, dict)
            )
            contradiction_lines.append(f"- {topic}: {claims}")
    contradictions_text = "\n".join(contradiction_lines)
    evidence_text = await _evidence_excerpts(db, episode, steps)

    verdict = await review_episode_llm(
        title=episode.title,
        root_cause=episode.root_cause_summary,
        final_outcome=episode.final_outcome,
        steps_text=steps_text,
        contradictions_text=contradictions_text,
        evidence_text=evidence_text,
        tenant_id=tenant_id,
        db=db,
        episode_id=episode.id,
    )

    if verdict.get("transient_failure"):
        # Provider outage / budget block: persist NOTHING so the next
        # sweep retries this draft. Stamping the outage made a one-hour
        # blip a permanent "never reviewed" for the whole batch.
        return {
            "episode_id": str(episode.id),
            "verdict": "hold",
            "approved": False,
            "transient_failure": True,
        }

    # Re-read the row UNDER LOCK before writing anything. The LLM call
    # above takes ~14s and holds no locks; in that window a human may
    # have approved/rejected this draft or the dedup sweep may have
    # superseded it. Whatever changed the state wins — stamping (or
    # worse, auto-approving) over a concurrent decision is the
    # overwrite bug that kept auto_approve blocked. populate_existing
    # is load-bearing: without it the identity map hands back the stale
    # pre-review attributes and the check is vacuous. The lock is held
    # only from here until the caller's per-episode commit.
    fresh = (
        await db.execute(
            select(Episode)
            .where(Episode.id == episode.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if (
        fresh is None
        or fresh.reviewer_state != "pending_review"
        or fresh.ai_review is not None
    ):
        return {
            "episode_id": str(episode.id),
            "verdict": verdict.get("verdict"),
            "approved": False,
            "skipped_state_changed": True,
        }

    passes, failed_floors = passes_auto_approve_floors(episode, verdict)
    approved = mode == "auto_approve" and passes

    episode.ai_review = {
        **verdict,
        "mode": mode,
        "auto_approved": approved,
        "failed_floors": failed_floors,
        "reviewed_at": datetime.now(UTC).isoformat(),
    }

    if approved:
        episode.status = "approved"
        episode.reviewer_state = "approved"
        # reviewer_user_id deliberately stays NULL: nobody signed this.
        from contextedge.services.event_log_service import append_operational_event

        await append_operational_event(
            db,
            tenant_id=tenant_id,
            entity_type="episode",
            entity_id=episode.id,
            event_type="episode.ai_approved",
            payload={
                "confidence": verdict.get("confidence"),
                "reasons": verdict.get("reasons"),
                "prompt_version": verdict.get("prompt_version"),
            },
        )
        # Deliberately NO task dispatch here: this function runs inside
        # an open transaction, and a message sent now can be consumed
        # before (or without) the commit landing — the signature task
        # would read the pending state and no-op WITHOUT retry. The
        # caller commits first, then dispatches (see ai_review_episodes).

    await db.flush()
    return {
        "episode_id": str(episode.id),
        "verdict": verdict.get("verdict"),
        "approved": approved,
        "domain_id": str(episode.domain_id) if episode.domain_id else None,
    }
