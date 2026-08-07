"""Episode reconstruction from correlated evidence.

Large correlation clusters (>``MAX_ITEMS_PER_CALL`` evidence items) are split
into chunks and extracted in parallel map-style calls. Each chunk produces a
list of candidate episodes which are concatenated into the final result.

We deliberately do **not** run a cross-chunk LLM synthesis pass today:

- Downstream pattern-mining and correlation services already dedupe
  overlapping incidents at the episode/pattern layer.
- A synthesis call would roughly double LLM spend on large clusters for
  marginal quality improvement on current workloads.

If cross-chunk duplication shows up as a real problem (same incident split
across two episodes from adjacent chunks), add a reduce pass that sees only
episode-level summaries — evidence bodies should never re-enter the prompt.

Prompt text lives in ``contextedge.ai.prompts.episode`` (registry-
versioned, A/B-routable per tenant).
"""

from __future__ import annotations

import uuid as _uuid
from typing import Any

import structlog

from contextedge.ai.extractors.episode_schema import validate_episode
from contextedge.ai.fencing import fence_untrusted
from contextedge.ai.prompts import get_prompt
from contextedge.ai.provider import llm_complete_json
from contextedge.ai.text_salience import salient_slice

logger = structlog.get_logger()

# Maximum evidence items per single LLM call. 20 items at
# ``PER_ITEM_CHAR_LIMIT`` each ≈ 40K chars ≈ 10K tokens of evidence —
# leaves plenty of room for the prompt, schema, and completion inside a
# standard 128K-token model. Tuning this down reduces per-call cost at
# the expense of more calls for large clusters; tuning up risks
# truncation on clusters of long email/ticket bodies.
MAX_ITEMS_PER_CALL = 20

# Per-item body truncation. Matches the value that has worked well for
# ticket/chat/email bodies since before chunking was added.
PER_ITEM_CHAR_LIMIT = 2000


def _format_evidence_block(evidence_items: list[dict]) -> str:
    """Each item is labelled [ev-N] so the model can ground episodes and
    steps in specific evidence; ``source_role`` (ticket / working
    discussion / external communication …) rides next to the raw source
    so synthesis can weight them differently."""
    out = ""
    for i, item in enumerate(evidence_items):
        out += f"\n--- Evidence [ev-{i + 1}] ---\n"
        out += f"Source: {item.get('source_type', 'unknown')}"
        if item.get("source_role"):
            out += f" ({item['source_role']})"
        out += "\n"
        if item.get("timestamp"):
            out += f"Time: {item['timestamp']}\n"
        if item.get("title"):
            out += f"Title: {item['title']}\n"
        content_val = (
            item.get("body")
            or item.get("body_text")
            or item.get("body_summary")
            or ""
        )
        out += f"Content: {salient_slice(content_val, PER_ITEM_CHAR_LIMIT)}\n"
    return out


def _translate_refs(raw_refs: object, ref_map: dict[str, str]) -> list[str] | None:
    """Model-emitted [ev-N] labels → real evidence UUID strings. Unknown
    or malformed labels are dropped (the model must never mint evidence);
    None when nothing valid remains, so callers can distinguish "model
    attributed nothing" from "model attributed these"."""
    if not isinstance(raw_refs, list):
        return None
    translated = [
        ref_map[str(label).strip().strip("[]")]
        for label in raw_refs
        if str(label).strip().strip("[]") in ref_map
    ]
    return translated or None


def _chunk(items: list[dict], size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


async def _extract_from_chunk(
    evidence_items: list[dict],
    *,
    tenant_id: _uuid.UUID | str | None = None,
    db: Any | None = None,
    prompt_version: str | None = None,
) -> list[dict]:
    if prompt_version is not None:
        from contextedge.ai.prompts import get_prompt_version

        prompt = get_prompt_version("episode", prompt_version)
    else:
        prompt = get_prompt("episode", tenant_id)
    user = prompt.format_user(evidence_text=fence_untrusted(_format_evidence_block(evidence_items)))
    result = await llm_complete_json(
        user,
        task="extraction",
        system_prompt=prompt.system,
        tenant_id=tenant_id,
        db=db,
        prompt_name=prompt.name,
        prompt_version=prompt.version,
    )
    if not isinstance(result, dict):
        return []

    episodes = result.get("episodes", [])
    if not episodes and "title" in result:
        # Fallback for old single-episode format if LLM ignores the list
        # instruction — rare but observed on older model snapshots.
        episodes = [result]

    ref_map = {
        f"ev-{i + 1}": str(item.get("evidence_id"))
        for i, item in enumerate(evidence_items)
        if item.get("evidence_id")
    }
    validated: list[dict] = []
    for raw_ep in episodes:
        if not isinstance(raw_ep, dict):
            continue
        for step in raw_ep.get("steps", []) if isinstance(raw_ep.get("steps"), list) else []:
            if isinstance(step, dict):
                step.setdefault("failed_flag", step.get("result_state") == "failure")
                step.setdefault("successful_flag", step.get("result_state") == "success")
        # Contradiction accounts cite [ev-N] labels too — translate them
        # to real evidence ids the same way, dropping minted references.
        raw_contradictions = raw_ep.get("contradictions")
        if isinstance(raw_contradictions, list):
            for contradiction in raw_contradictions:
                if not isinstance(contradiction, dict):
                    continue
                for account in contradiction.get("accounts", []) or []:
                    if isinstance(account, dict):
                        refs = _translate_refs([account.get("evidence_ref")], ref_map)
                        account["evidence_id"] = refs[0] if refs else None
        ep = validate_episode(raw_ep)
        if ep is None:
            continue
        ep["evidence_refs"] = _translate_refs(ep.get("evidence_refs"), ref_map)
        for step in ep.get("steps", []):
            step["evidence_refs"] = _translate_refs(step.get("evidence_refs"), ref_map)
        validated.append(ep)

    return validated


async def reconstruct_episode(
    evidence_items: list[dict],
    *,
    tenant_id: _uuid.UUID | str | None = None,
    db: Any | None = None,
    prompt_version: str | None = None,
) -> list[dict]:
    """Reconstruct structured episodes from evidence items.

    Clusters of ``MAX_ITEMS_PER_CALL`` items or fewer are sent in a single
    LLM call (preserves the pre-chunking behavior). Larger clusters are
    split into chunks and extracted one chunk at a time; the resulting
    episode lists are concatenated.

    Args:
        evidence_items: list of dicts with keys: title, body, source_type,
            timestamp, evidence_id.

    Returns:
        List of structured episode dicts.
    """
    if not evidence_items:
        return []

    if len(evidence_items) <= MAX_ITEMS_PER_CALL:
        return await _extract_from_chunk(
            evidence_items, tenant_id=tenant_id, db=db, prompt_version=prompt_version
        )

    chunks = list(_chunk(evidence_items, MAX_ITEMS_PER_CALL))
    logger.info(
        "episode_extractor.chunked",
        evidence_count=len(evidence_items),
        chunk_count=len(chunks),
        max_items_per_call=MAX_ITEMS_PER_CALL,
    )

    all_episodes: list[dict] = []
    for chunk in chunks:
        all_episodes.extend(
            await _extract_from_chunk(
                chunk, tenant_id=tenant_id, db=db, prompt_version=prompt_version
            )
        )
    return all_episodes
