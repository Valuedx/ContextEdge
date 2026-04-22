"""Tests for ai/observability.py — LLM usage recording + message shaping.

These are pure-Python unit tests (no network, no DB) covering the shape
guarantees the admin dashboard and cost alerts rely on.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from contextedge.ai.observability import (
    build_messages,
    extract_usage,
    record_llm_usage,
)


# ---------------------------------------------------------------------------
# build_messages — prompt shaping
# ---------------------------------------------------------------------------


def test_build_messages_system_and_user_with_cache():
    messages = build_messages("system text", "user text", cache_system=True)
    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    # Content blocks are a list when cache_system is True — matches
    # Anthropic's content-block format LiteLLM expects.
    assert isinstance(messages[0]["content"], list)
    block = messages[0]["content"][0]
    assert block["type"] == "text"
    assert block["text"] == "system text"
    assert block["cache_control"] == {"type": "ephemeral"}
    assert messages[1] == {"role": "user", "content": "user text"}


def test_build_messages_no_cache_uses_plain_string():
    """When cache_system=False we emit a plain string content so legacy
    OpenAI calls that don't understand content blocks still work."""
    messages = build_messages("sys", "usr", cache_system=False)
    assert messages[0] == {"role": "system", "content": "sys"}


def test_build_messages_no_system_returns_user_only():
    """Legacy single-message calls from unmigrated callers still work."""
    messages = build_messages(None, "just user")
    assert messages == [{"role": "user", "content": "just user"}]


def test_build_messages_empty_system_omits_system_block():
    """Empty string treated as no system prompt."""
    messages = build_messages("", "usr")
    assert len(messages) == 1
    assert messages[0]["role"] == "user"


# ---------------------------------------------------------------------------
# extract_usage — provider-agnostic token extraction
# ---------------------------------------------------------------------------


def test_extract_usage_openai_cached():
    """OpenAI surfaces cached tokens under prompt_tokens_details."""
    response = SimpleNamespace(
        usage=SimpleNamespace(
            prompt_tokens=100,
            completion_tokens=50,
            prompt_tokens_details=SimpleNamespace(cached_tokens=75),
        )
    )
    usage = extract_usage(response)
    assert usage == {
        "prompt_tokens": 100,
        "completion_tokens": 50,
        "cached_tokens": 75,
        "total_tokens": 150,
    }


def test_extract_usage_anthropic_cached():
    """Anthropic surfaces cached tokens at usage.cache_read_input_tokens."""
    response = SimpleNamespace(
        usage=SimpleNamespace(
            prompt_tokens=200,
            completion_tokens=80,
            cache_read_input_tokens=150,
        )
    )
    usage = extract_usage(response)
    assert usage["cached_tokens"] == 150


def test_extract_usage_no_cache_fields_returns_zero():
    """Missing cache fields must not raise."""
    response = SimpleNamespace(usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5))
    usage = extract_usage(response)
    assert usage == {
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "cached_tokens": 0,
        "total_tokens": 15,
    }


def test_extract_usage_no_usage_attr_returns_all_zero():
    """Some error responses have no usage at all — must not raise."""
    response = SimpleNamespace()
    assert extract_usage(response) == {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "cached_tokens": 0,
        "total_tokens": 0,
    }


def test_extract_usage_coerces_non_int_values():
    """Providers occasionally return strings or None. Never raise."""
    response = SimpleNamespace(
        usage=SimpleNamespace(
            prompt_tokens="not a number",
            completion_tokens=None,
        )
    )
    usage = extract_usage(response)
    assert usage["prompt_tokens"] == 0
    assert usage["completion_tokens"] == 0


# ---------------------------------------------------------------------------
# record_llm_usage — writes Prometheus, log, optional operational event
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_record_llm_usage_without_db_skips_event_persist():
    """No db passed → no operational event written, but counters still tick."""
    with patch("contextedge.ai.observability.LLM_TOKENS_TOTAL") as tokens_counter, \
         patch("contextedge.ai.observability.LLM_REQUESTS_TOTAL") as req_counter:
        await record_llm_usage(
            tenant_id=uuid4(),
            model="gpt-4o-mini",
            task="classification",
            usage={"prompt_tokens": 100, "completion_tokens": 50, "cached_tokens": 80, "total_tokens": 150},
        )
        # 3 token-type calls + 1 request call
        assert tokens_counter.labels.call_count == 3
        req_counter.labels.assert_called_once()


@pytest.mark.asyncio
async def test_record_llm_usage_with_db_writes_operational_event():
    """db + tenant_id passed → operational event persisted via append_operational_event."""
    mock_event = AsyncMock()
    with patch("contextedge.services.event_log_service.append_operational_event", mock_event), \
         patch("contextedge.ai.observability.LLM_TOKENS_TOTAL"), \
         patch("contextedge.ai.observability.LLM_REQUESTS_TOTAL"):
        tid = uuid4()
        await record_llm_usage(
            tenant_id=tid,
            model="gpt-4o",
            task="extraction",
            usage={"prompt_tokens": 500, "completion_tokens": 200, "cached_tokens": 300, "total_tokens": 700},
            db=SimpleNamespace(),
            duration_ms=1234,
        )
        mock_event.assert_awaited_once()
        call_kwargs = mock_event.call_args.kwargs
        assert call_kwargs["tenant_id"] == tid
        assert call_kwargs["entity_type"] == "llm_usage"
        assert call_kwargs["event_type"] == "llm.usage"
        payload = call_kwargs["payload"]
        assert payload["model"] == "gpt-4o"
        assert payload["task"] == "extraction"
        assert payload["cached_tokens"] == 300
        assert payload["duration_ms"] == 1234


@pytest.mark.asyncio
async def test_record_llm_usage_swallows_event_persist_errors():
    """A broken event_log_service must not break the actual LLM call path."""
    with patch(
        "contextedge.services.event_log_service.append_operational_event",
        AsyncMock(side_effect=RuntimeError("db down")),
    ), patch("contextedge.ai.observability.LLM_TOKENS_TOTAL"), \
       patch("contextedge.ai.observability.LLM_REQUESTS_TOTAL"):
        # Should not raise
        await record_llm_usage(
            tenant_id=uuid4(),
            model="gpt-4o",
            task="extraction",
            usage={"prompt_tokens": 10, "completion_tokens": 5, "cached_tokens": 0, "total_tokens": 15},
            db=SimpleNamespace(),
        )


@pytest.mark.asyncio
async def test_record_llm_usage_extracts_usage_from_response_when_dict_missing():
    """Caller passes ``response``; helper should extract usage from it."""
    response = SimpleNamespace(
        usage=SimpleNamespace(
            prompt_tokens=42,
            completion_tokens=21,
            prompt_tokens_details=SimpleNamespace(cached_tokens=20),
        )
    )
    with patch("contextedge.ai.observability.LLM_TOKENS_TOTAL") as counter, \
         patch("contextedge.ai.observability.LLM_REQUESTS_TOTAL"):
        returned = await record_llm_usage(
            tenant_id=None,
            model="gpt-4o",
            task="extraction",
            response=response,
        )
        assert returned["prompt_tokens"] == 42
        assert returned["cached_tokens"] == 20


@pytest.mark.asyncio
async def test_record_llm_usage_handles_no_tenant_id():
    """Batch embeddings and some tasks don't carry tenant — use "unknown" label."""
    with patch("contextedge.ai.observability.LLM_TOKENS_TOTAL") as counter, \
         patch("contextedge.ai.observability.LLM_REQUESTS_TOTAL"):
        await record_llm_usage(
            tenant_id=None,
            model="text-embedding-3-small",
            task="embedding",
            usage={"prompt_tokens": 100, "completion_tokens": 0, "cached_tokens": 0, "total_tokens": 100},
        )
        # First call's first arg should be "unknown" for tenant_id
        first_call_labels = counter.labels.call_args_list[0][0]
        assert first_call_labels[0] == "unknown"
