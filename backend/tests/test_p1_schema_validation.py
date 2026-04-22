"""Tests for llm_complete_json_validated — schema validation + bounded retry (W7-9.2)."""

from typing import Literal
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import BaseModel, Field

from contextedge.ai.provider import (
    _build_repair_prompt,
    _format_validation_errors,
    llm_complete_json_validated,
)


class _Incident(BaseModel):
    """Minimal schema for tests — mirrors the shape real extractors use."""

    title: str = Field(..., min_length=1)
    severity: Literal["low", "medium", "high"]
    resolved: bool = False


@pytest.mark.asyncio
async def test_valid_response_passes_on_first_call():
    with patch(
        "contextedge.ai.provider.llm_complete_json",
        new=AsyncMock(return_value={"title": "VPN outage", "severity": "high", "resolved": True}),
    ) as m:
        result = await llm_complete_json_validated("describe the incident", _Incident)

    assert m.await_count == 1
    assert isinstance(result, _Incident)
    assert result.title == "VPN outage"
    assert result.severity == "high"


@pytest.mark.asyncio
async def test_invalid_response_triggers_one_retry_and_succeeds():
    """First response violates the enum; retry with repair prompt fixes it."""
    bad = {"title": "VPN outage", "severity": "catastrophic", "resolved": False}
    good = {"title": "VPN outage", "severity": "high", "resolved": False}

    with patch(
        "contextedge.ai.provider.llm_complete_json",
        new=AsyncMock(side_effect=[bad, good]),
    ) as m:
        result = await llm_complete_json_validated("prompt", _Incident)

    assert m.await_count == 2
    assert result.severity == "high"


@pytest.mark.asyncio
async def test_retry_budget_zero_raises_on_first_failure():
    bad = {"title": "", "severity": "high"}
    with patch(
        "contextedge.ai.provider.llm_complete_json",
        new=AsyncMock(return_value=bad),
    ) as m:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            await llm_complete_json_validated("p", _Incident, max_retries=0)
    assert m.await_count == 1


@pytest.mark.asyncio
async def test_retry_that_also_fails_raises_valueerror():
    """When retry *also* fails validation, we wrap the final error in
    a ValueError that names the schema — easier for callers to catch
    than chasing a Pydantic ValidationError up the stack."""
    bad1 = {"title": "x", "severity": "nope"}
    bad2 = {"title": "x", "severity": "also-nope"}

    with patch(
        "contextedge.ai.provider.llm_complete_json",
        new=AsyncMock(side_effect=[bad1, bad2]),
    ):
        with pytest.raises(ValueError, match="_Incident.*validation"):
            await llm_complete_json_validated("p", _Incident)


@pytest.mark.asyncio
async def test_unparseable_json_triggers_retry():
    """llm_complete_json raises ValueError for un-parseable output. The
    validator must treat that the same as a validation failure and
    retry once."""
    good = {"title": "VPN outage", "severity": "low"}

    async def side_effect(*args, **kwargs):
        if side_effect.calls == 0:
            side_effect.calls += 1
            raise ValueError("LLM returned invalid JSON for task 'extraction'")
        return good

    side_effect.calls = 0

    with patch(
        "contextedge.ai.provider.llm_complete_json",
        new=AsyncMock(side_effect=side_effect),
    ) as m:
        result = await llm_complete_json_validated("p", _Incident)

    assert m.await_count == 2
    assert result.severity == "low"


@pytest.mark.asyncio
async def test_repair_prompt_contains_schema_and_prior_response():
    """A regression guard: the repair prompt must include both the
    schema shape (so the model knows what to produce) and the raw prior
    response (so it can see what it got wrong). Without either, the
    retry is unlikely to succeed."""
    captured: list[str] = []
    bad = {"title": "x", "severity": "wrong"}
    good = {"title": "x", "severity": "low"}

    async def fake(prompt, **kwargs):
        captured.append(prompt)
        return bad if len(captured) == 1 else good

    with patch(
        "contextedge.ai.provider.llm_complete_json",
        new=AsyncMock(side_effect=fake),
    ):
        await llm_complete_json_validated("original prompt here", _Incident)

    assert len(captured) == 2
    repair = captured[1]
    assert "original prompt here" in repair
    # The Pydantic-generated schema should be referenced by title or name.
    assert "_Incident" in repair or "title" in repair
    # The raw-invalid response is echoed back so the model can diff.
    assert "wrong" in repair


def test_format_validation_errors_limits_output():
    from pydantic import ValidationError

    class _Strict(BaseModel):
        a: int
        b: int
        c: int
        d: int
        e: int
        f: int

    try:
        _Strict.model_validate({})
    except ValidationError as err:
        rendered = _format_validation_errors(err, limit=3)
        lines = rendered.split("\n")
        # Three bullets + one "... N more" summary line.
        assert len(lines) == 4
        assert "more" in lines[-1]


def test_format_validation_errors_no_summary_when_under_limit():
    from pydantic import ValidationError

    class _Strict(BaseModel):
        a: int

    try:
        _Strict.model_validate({})
    except ValidationError as err:
        rendered = _format_validation_errors(err, limit=3)
        assert "more" not in rendered


def test_build_repair_prompt_handles_no_prior_response():
    """When the first call failed to parse JSON at all, there's no raw
    response to echo back — the prompt must still be well-formed."""
    repair = _build_repair_prompt(
        "do the thing", raw_output=None, err=None, schema=_Incident,
    )
    assert "could not be parsed" in repair
    assert "do the thing" in repair
