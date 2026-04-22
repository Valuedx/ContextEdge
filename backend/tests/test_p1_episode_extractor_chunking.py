"""Tests for episode_extractor chunking + token-budget guard (W3-4.3).

The extractor must:
- call the LLM exactly once when the cluster fits in ``MAX_ITEMS_PER_CALL``
- split into ``ceil(n/MAX_ITEMS_PER_CALL)`` calls for larger clusters
- truncate each evidence body to ``PER_ITEM_CHAR_LIMIT`` so a pathologically
  long item can't blow the per-call token budget
- return the concatenation of per-chunk episode lists
- short-circuit on empty input without calling the LLM at all
"""

from unittest.mock import AsyncMock, patch

import pytest

from contextedge.ai.extractors.episode_extractor import (
    MAX_ITEMS_PER_CALL,
    PER_ITEM_CHAR_LIMIT,
    reconstruct_episode,
)


def _item(i: int, body: str = "body text") -> dict:
    return {
        "title": f"Evidence {i}",
        "body": body,
        "source_type": "ticket",
        "timestamp": f"2026-04-{(i % 28) + 1:02d}T10:00:00Z",
        "evidence_id": f"ev-{i}",
    }


def _ep(title: str = "ep") -> dict:
    return {
        "title": title,
        "root_cause_summary": None,
        "final_outcome": None,
        "overall_confidence": 0.7,
        "steps": [
            {
                "step_order": 1,
                "step_type": "complaint",
                "text": "user reports issue",
                "result_state": "unknown",
                "confidence": 0.5,
            }
        ],
    }


@pytest.mark.asyncio
async def test_empty_input_skips_llm_entirely():
    with patch(
        "contextedge.ai.extractors.episode_extractor.llm_complete_json",
        new=AsyncMock(return_value={"episodes": []}),
    ) as m:
        result = await reconstruct_episode([])
    assert result == []
    m.assert_not_awaited()


@pytest.mark.asyncio
async def test_small_cluster_uses_single_call():
    items = [_item(i) for i in range(MAX_ITEMS_PER_CALL)]
    with patch(
        "contextedge.ai.extractors.episode_extractor.llm_complete_json",
        new=AsyncMock(return_value={"episodes": [_ep("one")]}),
    ) as m:
        result = await reconstruct_episode(items)
    assert m.await_count == 1
    assert len(result) == 1
    assert result[0]["title"] == "one"


@pytest.mark.asyncio
async def test_just_over_threshold_splits_into_two_calls():
    items = [_item(i) for i in range(MAX_ITEMS_PER_CALL + 1)]
    with patch(
        "contextedge.ai.extractors.episode_extractor.llm_complete_json",
        new=AsyncMock(side_effect=[{"episodes": [_ep("a")]}, {"episodes": [_ep("b")]}]),
    ) as m:
        result = await reconstruct_episode(items)
    assert m.await_count == 2
    # Episodes are concatenated in chunk order.
    assert [ep["title"] for ep in result] == ["a", "b"]


@pytest.mark.asyncio
async def test_large_cluster_splits_into_correct_chunk_count():
    # 3 full chunks + 1 partial chunk
    n = 3 * MAX_ITEMS_PER_CALL + 5
    items = [_item(i) for i in range(n)]
    with patch(
        "contextedge.ai.extractors.episode_extractor.llm_complete_json",
        new=AsyncMock(return_value={"episodes": [_ep()]}),
    ) as m:
        await reconstruct_episode(items)
    assert m.await_count == 4


@pytest.mark.asyncio
async def test_long_body_is_truncated_to_per_item_char_limit():
    """A single evidence item with a huge body must not blow the per-call
    token budget. The extractor truncates bodies to PER_ITEM_CHAR_LIMIT
    before building the prompt."""
    huge = "x" * (PER_ITEM_CHAR_LIMIT * 10)
    items = [_item(0, body=huge)]
    captured = {}

    async def fake_llm(prompt, **kwargs):
        captured["prompt"] = prompt
        return {"episodes": [_ep()]}

    with patch(
        "contextedge.ai.extractors.episode_extractor.llm_complete_json",
        new=AsyncMock(side_effect=fake_llm),
    ):
        await reconstruct_episode(items)

    body_part = captured["prompt"].split("Content: ", 1)[1]
    # The body in the prompt must not exceed PER_ITEM_CHAR_LIMIT (plus a
    # trailing newline and the rest of the prompt scaffolding).
    body_line = body_part.split("\n", 1)[0]
    assert len(body_line) <= PER_ITEM_CHAR_LIMIT


@pytest.mark.asyncio
async def test_chunked_result_preserves_step_defaults():
    """Step defaults (failed_flag/successful_flag/confidence) must be
    applied to steps from every chunk — not just the first."""
    items = [_item(i) for i in range(MAX_ITEMS_PER_CALL + 1)]

    ep_no_defaults = {
        "title": "x",
        "steps": [{"step_order": 1, "step_type": "action", "text": "t", "result_state": "failure"}],
    }
    with patch(
        "contextedge.ai.extractors.episode_extractor.llm_complete_json",
        new=AsyncMock(
            side_effect=[{"episodes": [ep_no_defaults]}, {"episodes": [ep_no_defaults]}]
        ),
    ):
        result = await reconstruct_episode(items)

    assert len(result) == 2
    for ep in result:
        step = ep["steps"][0]
        assert step["failed_flag"] is True
        assert step["successful_flag"] is False
        assert step["confidence"] == 0.5


@pytest.mark.asyncio
async def test_legacy_single_episode_dict_still_supported():
    """Older model snapshots sometimes returned a single episode dict
    instead of ``{"episodes": [...]}``. The fallback path must still work."""
    items = [_item(0)]
    legacy_response = _ep("legacy")
    with patch(
        "contextedge.ai.extractors.episode_extractor.llm_complete_json",
        new=AsyncMock(return_value=legacy_response),
    ):
        result = await reconstruct_episode(items)
    assert len(result) == 1
    assert result[0]["title"] == "legacy"


@pytest.mark.asyncio
async def test_max_items_per_call_constant_is_sane():
    """Guardrail: if someone bumps this to something unsafe (e.g. 200)
    chunking stops protecting us from context overflow. Keep it in a
    reasonable band."""
    assert 5 <= MAX_ITEMS_PER_CALL <= 50
    assert 500 <= PER_ITEM_CHAR_LIMIT <= 10_000
