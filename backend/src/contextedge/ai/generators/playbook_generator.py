"""Playbook candidate generation from pattern clusters.

Prompt text lives in ``contextedge.ai.prompts.playbook`` (registry-
versioned, A/B-routable per tenant).
"""

from __future__ import annotations

import uuid as _uuid
from typing import Any

from contextedge.ai.prompts import get_prompt
from contextedge.ai.provenance import GENERATION_PROVENANCE_KEY, generation_provenance
from contextedge.ai.provider import llm_complete_json


async def generate_playbook_candidate(
    pattern_title: str,
    pattern_description: str,
    episode_count: int,
    episode_summaries: list[dict],
    negative_knowledge: list[str],
    *,
    knowledge_sources: list[Any] | None = None,
    tenant_id: _uuid.UUID | str | None = None,
    db: Any | None = None,
) -> dict:
    """Generate a playbook candidate from pattern analysis.

    ``knowledge_sources`` are the approved KB/SOP documents retrieved for
    this pattern. They are passed as a *distinct* input rather than
    folded into the episode summaries, because the prompt has to treat
    them differently: knowledge is normative (what should be done) and
    episodes are empirical (what was done). Merging them would erase the
    distinction the reviewer needs to adjudicate a disagreement.

    Episodes are labelled ``[ep-N]`` so steps can cite them the same way
    they cite ``[kb-N]`` knowledge sections.
    """
    summaries_text = format_episode_summaries(episode_summaries)

    neg_text = (
        "\n".join(f"- {nk}" for nk in negative_knowledge[:20])
        if negative_knowledge
        else "None identified"
    )

    from contextedge.services.knowledge_retrieval_service import (
        format_knowledge_block,
    )

    knowledge_text = format_knowledge_block(knowledge_sources or [])

    prompt = get_prompt("playbook", tenant_id)
    format_kwargs: dict[str, Any] = {
        "pattern_title": pattern_title,
        "pattern_description": pattern_description or "",
        "episode_count": episode_count,
        "episode_summaries": summaries_text,
        "negative_knowledge": neg_text,
    }
    # Older prompt versions have no knowledge slot; a tenant pinned to v1
    # or v2 via variant routing must keep working rather than raising on
    # an unexpected format key.
    if "{knowledge_sources}" in prompt.user_template:
        format_kwargs["knowledge_sources"] = knowledge_text

    user = prompt.format_user(**format_kwargs)
    ref_map = _build_ref_map(knowledge_sources or [], episode_summaries)
    result = await llm_complete_json(
        user,
        # Its own task name, not "extraction". A playbook is not an
        # extraction — it is the longest single output the system
        # produces — and sharing a label meant it shared a token budget
        # sized for something else, and disappeared into someone else's
        # line on the cost dashboard.
        task="playbook",
        system_prompt=prompt.system,
        tenant_id=tenant_id,
        db=db,
        prompt_name=prompt.name,
        prompt_version=prompt.version,
    )
    if isinstance(result, dict):
        validate_source_refs(result, ref_map)
        classify_step_grounding(result)
        sanitize_branching_logic(result)
        # F5: stamped by the caller after validation, so the model can neither
        # supply nor influence the record of what produced it.
        result[GENERATION_PROVENANCE_KEY] = generation_provenance(prompt, task="playbook")
    return result


BEST_PRACTICE_REASON = (
    "Generated from industry/support engineering best practices; "
    "not explicitly present in the source."
)


def format_episode_summaries(episode_summaries: list[dict] | None) -> str:
    """Render episodes for the playbook prompt, including the mail-thread
    solution under each ``[ep-N]``.

    Title / root cause / outcome alone was how a working email-thread
    fix never reached generation. Observed steps and thread excerpts
    stay under the episode so KB remains a distinct normative input.
    """
    if not episode_summaries:
        return "None found. Base the playbook on approved knowledge only."
    blocks: list[str] = []
    for index, episode in enumerate(episode_summaries[:10], start=1):
        lines = [f"[ep-{index}] {episode.get('title') or 'Untitled'}"]
        if episode.get("root_cause"):
            lines.append(f"   Root cause: {episode['root_cause']}")
        if episode.get("outcome"):
            lines.append(f"   Outcome: {episode['outcome']}")
        steps = episode.get("steps") or []
        if isinstance(steps, list) and steps:
            lines.append("   Observed steps (from mail thread):")
            for step in steps:
                if isinstance(step, dict):
                    kind = step.get("type") or "action"
                    text = str(step.get("text") or "").strip()
                    observation = str(step.get("observation") or "").strip()
                else:
                    kind, text, observation = "action", str(step).strip(), ""
                if not text:
                    continue
                line = f"     - [{kind}] {text}"
                if observation:
                    line += f" (observed: {observation})"
                lines.append(line)
        snippets = episode.get("thread_solutions") or []
        if isinstance(snippets, list) and snippets:
            lines.append("   Mail-thread solution:")
            for snippet in snippets:
                text = str(snippet or "").strip()
                if text:
                    lines.append(f"     {text}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) if blocks else "None found. Base the playbook on approved knowledge only."


def _unreachable_orders(orders: set[int], points: list[dict]) -> set[int]:
    """Step orders no execution path visits.

    Traversal, not arithmetic: several points may share one anchor (the
    switch shape — one diagnosis routing to several remedies), so a step
    that one point jumps over is often reached by its sibling's branch.
    Judging points one at a time reports correct playbooks as broken.
    """
    if not orders:
        return set()
    by_anchor: dict[int, set[int]] = {}
    for point in points:
        anchor = point.get("after_step")
        if anchor not in orders:
            continue
        targets = {
            t
            for t in (point.get("if_true_goto"), point.get("if_false_goto"))
            if t in orders
        }
        by_anchor.setdefault(anchor, set()).update(targets)

    start = min(orders)
    reached, worklist = {start}, [start]
    while worklist:
        step = worklist.pop()
        successors = by_anchor.get(step)
        if not successors:
            # No decision at this step: control falls through to the next.
            successors = {step + 1} if (step + 1) in orders else set()
        for nxt in successors:
            if nxt not in reached:
                reached.add(nxt)
                worklist.append(nxt)
    return orders - reached


def _skips_any(point: dict, stranded: set[int]) -> bool:
    """True when this point's jumps hop over one of the stranded steps."""
    anchor = point.get("after_step")
    if not isinstance(anchor, int):
        return False
    for target in (point.get("if_true_goto"), point.get("if_false_goto")):
        if isinstance(target, int) and any(anchor < s < target for s in stranded):
            return True
    return False


def sanitize_branching_logic(result: dict) -> dict[str, int]:
    """Drop decision points that cannot execute, in place.

    Same philosophy as ``validate_source_refs``: a structure that looks
    authoritative and resolves to nothing is worse than its absence,
    because it survives review on appearance. Auditing the 190 generated
    playbooks found 20 with branching defects — 39% of the 51 that branch
    at all: targets naming steps that do not exist, decision points whose
    true and false paths are identical (deciding nothing), and steps no
    path can reach.

    Repair, not rejection. The steps of such a playbook are usually fine
    and it is only ``decision_points`` that is junk, so failing the whole
    generation would discard good work over a bad appendix. Dropped
    points are counted and logged so a prompt that starts emitting them
    is visible in the counters rather than only in a reviewer's
    confusion.
    """
    counts = {"kept": 0, "dropped": 0}
    branching = result.get("branching_logic")
    if not isinstance(branching, dict):
        return counts
    points = branching.get("decision_points")
    if not isinstance(points, list):
        return counts

    orders = {
        step.get("order")
        for step in (result.get("steps") or [])
        if isinstance(step, dict) and isinstance(step.get("order"), int)
    }
    kept: list = []
    reasons: list[str] = []
    for point in points:
        if not isinstance(point, dict):
            counts["dropped"] += 1
            reasons.append("not_an_object")
            continue
        anchor = point.get("after_step")
        target_true = point.get("if_true_goto")
        target_false = point.get("if_false_goto")
        if anchor not in orders:
            counts["dropped"] += 1
            reasons.append("anchor_not_a_step")
            continue
        if any(t is not None and t not in orders for t in (target_true, target_false)):
            counts["dropped"] += 1
            reasons.append("target_not_a_step")
            continue
        if anchor in (target_true, target_false):
            # Jumping back to the step you just finished is an infinite
            # loop for anything that executes this literally.
            counts["dropped"] += 1
            reasons.append("self_loop")
            continue
        if target_true is not None and target_true == target_false:
            # Both paths land in the same place: the condition changes
            # nothing, and presenting it as a decision misleads.
            counts["dropped"] += 1
            reasons.append("decides_nothing")
            continue
        kept.append(point)
        counts["kept"] += 1

    # Stranded steps are the one defect that is invisible per point. Every
    # surviving point above is individually well-formed, yet together they
    # can leave a step no path reaches — seen live on "Process/Workflow
    # Stuck During Database-Related Plugin Update", where step 1 branched
    # to 3 or 4 and nothing ever reached step 2. The step still prints, so
    # a reader sees instructions the flow never visits.
    #
    # Repaired by dropping jumps, not by inventing them: removing a point
    # restores plain fall-through from its anchor, which can only reach
    # more steps, so this terminates. Rewriting a target would be guessing
    # at intent.
    while kept:
        stranded = _unreachable_orders(orders, kept)
        if not stranded:
            break
        culprit = next(
            (p for p in kept if _skips_any(p, stranded)),
            kept[0],
        )
        kept.remove(culprit)
        counts["kept"] -= 1  # it was counted as kept by the per-point pass
        counts["dropped"] += 1
        reasons.append("stranded_a_step")

    branching["decision_points"] = kept
    if counts["dropped"]:
        import structlog

        structlog.get_logger().warning(
            "playbook.invalid_decision_points_dropped",
            dropped=counts["dropped"],
            kept=counts["kept"],
            reasons=sorted(set(reasons)),
        )
    result["branching_validation"] = counts
    return counts


def classify_step_grounding(result: dict) -> dict[str, int]:
    """Deterministic grounded / best-practice classification, applied
    AFTER citation cleaning so it cannot be argued with.

    The rule is structural, not model-claimed: a step whose (validated)
    source_refs are non-empty is grounded; a step with none is a
    best-practice recommendation and is FORCED to carry the tags —
    including a step the model claimed was grounded but whose minted
    citations were just dropped. Never the reverse: a model may not
    label an evidenced step best_practice to dodge review scrutiny.

    ``result["grounding"]`` records the counts and a grounded_ratio.
    Best-practice steps can only lower or hold that ratio, never raise
    it — the spec's scoring rule, enforced by arithmetic.
    """
    counts = {"grounded": 0, "best_practice": 0}
    steps = [s for s in (result.get("steps") or []) if isinstance(s, dict)]
    for step in steps:
        if step.get("source_refs"):
            step["grounding_status"] = "grounded"
            step.setdefault("step_classification", "procedure")
            if step.get("reason") == BEST_PRACTICE_REASON:
                step.pop("reason", None)
            counts["grounded"] += 1
        else:
            step["grounding_status"] = "non_grounded"
            step["step_classification"] = "best_practice"
            step["confidence"] = "best_practice"
            step["reason"] = BEST_PRACTICE_REASON
            counts["best_practice"] += 1
    result["grounding"] = {
        **counts,
        "grounded_ratio": (
            round(counts["grounded"] / len(steps), 3) if steps else 0.0
        ),
    }
    return counts


# --- citation validation -----------------------------------------------------


def _build_ref_map(
    knowledge_sources: list[Any], episode_summaries: list[dict]
) -> dict[str, dict[str, str]]:
    """Labels the model was actually shown -> what they identify.

    Mirrors the episode extractor's contract: the model cites by label,
    and only labels that were supplied can survive.

    Values carry ``kind`` as well as ``id`` because the whole point of
    Phase 2 is that normative and empirical grounding are different
    claims. A resolved citation that has lost which one it was is only
    half a citation.
    """
    ref_map: dict[str, dict[str, str]] = {}
    for index, document in enumerate(knowledge_sources, start=1):
        evidence_id = getattr(document, "evidence_id", None)
        if evidence_id is not None:
            ref_map[f"kb-{index}"] = {
                "kind": "knowledge",
                "id": str(evidence_id),
                "title": str(getattr(document, "title", "") or "")[:200],
            }
    for index, episode in enumerate(episode_summaries[:10], start=1):
        episode_id = episode.get("id")
        if episode_id:
            ref_map[f"ep-{index}"] = {
                "kind": "episode",
                "id": str(episode_id),
                "title": str(episode.get("title") or "")[:200],
            }
    return ref_map


def validate_source_refs(result: dict, ref_map: dict[str, str]) -> dict[str, int]:
    """Drop citations that point at nothing, in place.

    A generated playbook cites ``[kb-1]`` / ``[ep-2]``. Nothing checked
    that those labels were ever supplied, so a model that invented
    ``kb-7`` produced a step carrying an authoritative-looking reference
    to a document that does not exist. **An unverified citation is worse
    than none**: it survives review precisely because it looks like
    provenance.

    Minted refs are removed and counted rather than silently dropped, so
    a prompt that starts hallucinating citations is visible in the
    counters instead of only in a reviewer's confusion.
    """
    counts = {"kept": 0, "dropped": 0}

    def _clean(raw: object) -> list[dict]:
        """Resolve labels to durable references.

        The label is translated, not merely validated: ``"kb-1"`` means
        nothing once the prompt that defined it is gone, so a persisted
        step citing a bare label is unreviewable a week later. The label
        is retained alongside the id for traceability back to the
        generation.
        """
        if not isinstance(raw, list):
            return []
        kept: list[dict] = []
        seen: set[str] = set()
        for label in raw:
            token = str(label).strip().strip("[]")
            entry = ref_map.get(token)
            if entry is None:
                counts["dropped"] += 1
                continue
            if entry["id"] in seen:
                continue
            seen.add(entry["id"])
            kept.append({"label": token, **entry})
            counts["kept"] += 1
        return kept

    for step in result.get("steps") or []:
        if isinstance(step, dict):
            step["source_refs"] = _clean(step.get("source_refs"))

    for conflict in result.get("conflicts") or []:
        if isinstance(conflict, dict):
            conflict["source_refs"] = _clean(conflict.get("source_refs"))

    if counts["dropped"]:
        import structlog

        structlog.get_logger().warning(
            "playbook.minted_citations_dropped",
            dropped=counts["dropped"],
            kept=counts["kept"],
            supplied_labels=sorted(ref_map),
        )

    result["citation_validation"] = counts
    return counts
