"""Playbook candidate generation from pattern clusters.

Prompt text lives in ``contextedge.ai.prompts.playbook`` (registry-
versioned, A/B-routable per tenant).
"""

from __future__ import annotations

import uuid as _uuid
from typing import Any

from contextedge.ai.prompts import get_prompt
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
    summaries_text = ""
    for i, ep in enumerate(episode_summaries[:10]):
        summaries_text += f"\n[ep-{i + 1}] {ep.get('title', 'Untitled')}"
        if ep.get("root_cause"):
            summaries_text += f"\n   Root cause: {ep['root_cause']}"
        if ep.get("outcome"):
            summaries_text += f"\n   Outcome: {ep['outcome']}"

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
        task="extraction",
        system_prompt=prompt.system,
        tenant_id=tenant_id,
        db=db,
        prompt_name=prompt.name,
        prompt_version=prompt.version,
    )
    if isinstance(result, dict):
        validate_source_refs(result, ref_map)
    return result


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
