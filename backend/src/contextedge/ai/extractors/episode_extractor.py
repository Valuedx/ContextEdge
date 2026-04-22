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
"""

import structlog

from contextedge.ai.provider import llm_complete_json

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

EPISODE_PROMPT = """You are analyzing operational evidence from an IT troubleshooting case.
Your goal is to reconstruct one or more structured episodes from the providing evidence.

Logic for Grouping:
1. Combine evidence into a SINGLE episode IF AND ONLY IF they share the same context or category (e.g., cascading service failures, shared infrastructure logs, a shared root cause).
2. Separate evidence into MULTIPLE episodes if they represent distinct, unrelated operational problems (e.g., a "Billing Service crash" is distinct from a "VPN authentication failure" unless they are explicitly linked by a shared root cause).

Evidence items (ordered by time):
{evidence_text}

For each episode, identify a structured sequence of steps:
1. step_type: one of "complaint", "diagnostic", "hypothesis", "action", "observation", "failed_step", "remediation", "escalation", "outcome"
2. text: what happened in this step
3. observation: what was observed after this step (if applicable)
4. result_state: "success", "failure", "inconclusive", "unknown"
   NOTE: "failure" should ONLY be used when a troubleshooting ACTION or ATTEMPTED DIAGNOSTIC failed to execute (e.g., "Tried to ping but timed out").
   Do NOT mark complaints or diagnostics as "failure" just because they report a system crash. Reporting a crash is a "success".
5. confidence: 0.0-1.0 how confident you are this step actually occurred based on evidence

For each episode also extract:
- title: concise episode title
- root_cause_summary: root cause if identified (null if unclear)
- final_outcome: final resolution (null if unresolved)
- overall_confidence: 0.0-1.0 overall extraction confidence

Respond in JSON with a list of episodes:
{{
  "episodes": [
    {{
      "title": "...",
      "root_cause_summary": "..." or null,
      "final_outcome": "..." or null,
      "overall_confidence": 0.0-1.0,
      "steps": [
        {{
          "step_order": 1,
          "step_type": "...",
          "text": "...",
          "observation": "..." or null,
          "result_state": "...",
          "failed_flag": false,
          "successful_flag": false,
          "confidence": 0.0-1.0
        }}
      ]
    }}
  ]
}}

Preserve failed steps and uncertainty. Do not collapse ambiguity into polished summaries.
"""


def _format_evidence_block(evidence_items: list[dict]) -> str:
    out = ""
    for i, item in enumerate(evidence_items):
        out += f"\n--- Evidence {i + 1} ---\n"
        out += f"Source: {item.get('source_type', 'unknown')}\n"
        if item.get("timestamp"):
            out += f"Time: {item['timestamp']}\n"
        if item.get("title"):
            out += f"Title: {item['title']}\n"
        out += f"Content: {(item.get('body', '') or '')[:PER_ITEM_CHAR_LIMIT]}\n"
    return out


def _chunk(items: list[dict], size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


async def _extract_from_chunk(evidence_items: list[dict]) -> list[dict]:
    prompt = EPISODE_PROMPT.format(evidence_text=_format_evidence_block(evidence_items))
    result = await llm_complete_json(prompt, task="extraction")
    if not isinstance(result, dict):
        return []

    episodes = result.get("episodes", [])
    if not episodes and "title" in result:
        # Fallback for old single-episode format if LLM ignores the list
        # instruction — rare but observed on older model snapshots.
        episodes = [result]

    for ep in episodes:
        for step in ep.get("steps", []):
            step.setdefault("failed_flag", step.get("result_state") == "failure")
            step.setdefault("successful_flag", step.get("result_state") == "success")
            step.setdefault("confidence", 0.5)

    return episodes


async def reconstruct_episode(evidence_items: list[dict]) -> list[dict]:
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
        return await _extract_from_chunk(evidence_items)

    chunks = list(_chunk(evidence_items, MAX_ITEMS_PER_CALL))
    logger.info(
        "episode_extractor.chunked",
        evidence_count=len(evidence_items),
        chunk_count=len(chunks),
        max_items_per_call=MAX_ITEMS_PER_CALL,
    )

    all_episodes: list[dict] = []
    for chunk in chunks:
        all_episodes.extend(await _extract_from_chunk(chunk))
    return all_episodes
