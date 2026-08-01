"""E1 LLM resilience: breaker semantics, timeout counting, fallback."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from contextedge.ai.resilience import (
    CircuitBreaker,
    LlmCircuitOpenError,
)


class _Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_breaker_opens_after_threshold_and_half_opens():
    clock = _Clock()
    b = CircuitBreaker(threshold=3, cooldown=60.0, clock=clock)

    for _ in range(3):
        b.check("m")
        b.record_failure("m")
    with pytest.raises(LlmCircuitOpenError):
        b.check("m")

    # Cooldown elapses: exactly ONE probe allowed.
    clock.now = 61.0
    b.check("m")  # the probe
    with pytest.raises(LlmCircuitOpenError):
        b.check("m")  # concurrent caller during probe fails fast

    # Failed probe re-opens from now.
    b.record_failure("m")
    with pytest.raises(LlmCircuitOpenError):
        b.check("m")
    clock.now = 122.0
    b.check("m")
    b.record_success("m")
    b.check("m")  # closed again — no raise
    assert True


def test_success_resets_consecutive_failures():
    b = CircuitBreaker(threshold=3, cooldown=60.0, clock=_Clock())
    b.record_failure("m")
    b.record_failure("m")
    b.record_success("m")
    b.record_failure("m")
    b.record_failure("m")
    b.check("m")  # 2 consecutive, never reached 3 — still closed


def _response(text="ok"):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text))],
        usage=None,
        model="m",
    )


@pytest.mark.asyncio
async def test_fallback_serves_after_primary_failure(monkeypatch):
    from contextedge.ai import provider as provider_mod

    calls = []

    async def fake_completion(**kwargs):
        calls.append(kwargs["model"])
        if kwargs["model"] == "primary-model":
            raise RuntimeError("provider down")
        return _response("from fallback")

    monkeypatch.setattr(
        provider_mod.litellm, "acompletion", fake_completion
    )
    monkeypatch.setattr(
        provider_mod.settings, "llm_fallback_model", "fallback-model", raising=False
    )
    with patch(
        "contextedge.ai.provider.record_llm_usage", AsyncMock()
    ) as usage:
        out = await provider_mod.llm_complete(
            "hello", task="extraction", model="primary-model"
        )

    assert out == "from fallback"
    assert calls == ["primary-model", "fallback-model"]
    # Usage recorded against the model that actually served.
    assert usage.await_args.kwargs["model"] == "fallback-model"
    assert usage.await_args.kwargs["outcome"] == "ok"


@pytest.mark.asyncio
async def test_no_fallback_configured_raises_primary_error(monkeypatch):
    from contextedge.ai import provider as provider_mod

    async def fake_completion(**kwargs):
        raise RuntimeError("provider down")

    monkeypatch.setattr(provider_mod.litellm, "acompletion", fake_completion)
    monkeypatch.setattr(
        provider_mod.settings, "llm_fallback_model", None, raising=False
    )
    with patch("contextedge.ai.provider.record_llm_usage", AsyncMock()):
        with pytest.raises(RuntimeError, match="provider down"):
            await provider_mod.llm_complete(
                "hello", task="extraction", model="primary-model"
            )
