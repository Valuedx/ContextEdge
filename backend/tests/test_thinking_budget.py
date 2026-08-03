"""Per-prompt thinking budget.

Sized from measurement, not intuition: 72% of all recorded output tokens
were reasoning, and output bills ~8x input on gemini-2.5-flash. But a
controlled comparison showed capping is not uniformly safe, which is why
this ships configured for exactly one prompt.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from contextedge.ai.provider import resolve_thinking_budget
from contextedge.config import settings

GEMINI = "vertex_ai/gemini-2.5-flash"


@pytest.fixture
def budgets():
    original = settings.llm_thinking_budgets
    try:
        yield lambda mapping: setattr(settings, "llm_thinking_budgets", mapping)
    finally:
        settings.llm_thinking_budgets = original


# --- resolution --------------------------------------------------------------


def test_configured_prompt_gets_its_budget(budgets):
    budgets({"relevance": 0, "episode": 256})
    assert resolve_thinking_budget("relevance", GEMINI) == 0
    assert resolve_thinking_budget("episode", GEMINI) == 256


def test_unconfigured_prompt_keeps_dynamic_thinking(budgets):
    """None means "send nothing" — the provider keeps deciding, which is
    the behaviour that shipped. Adding the knob must change nothing until
    it is set."""
    budgets({"relevance": 0})
    assert resolve_thinking_budget("identity_adjudication", GEMINI) is None
    assert resolve_thinking_budget("decision", GEMINI) is None
    assert resolve_thinking_budget(None, GEMINI) is None


def test_identity_adjudication_is_deliberately_unconfigured():
    """Measured: the verdict was identical at every budget, but
    confidence moved 0.95 -> 0.80, and
    identity_service.AUTO_LINK_THRESHOLDS["person"] is 0.95. Capping here
    silently converts auto-links into review-queue items, so it must not
    ship configured without a matching threshold change.
    """
    from contextedge.services.identity_service import AUTO_LINK_THRESHOLDS

    assert "identity_adjudication" not in (settings.llm_thinking_budgets or {})
    assert AUTO_LINK_THRESHOLDS["person"] == 0.95


def test_budget_is_withheld_from_models_that_cannot_reason(budgets):
    """Sending the parameter to a non-reasoning model is a 400, and the
    model changes under a prompt via routing and the fallback."""
    budgets({"relevance": 0})
    assert resolve_thinking_budget("relevance", "gpt-4o-mini") is None
    assert resolve_thinking_budget("relevance", "") is None


def test_capability_probe_never_fails_the_call(budgets):
    """An unknown model makes litellm raise. That must degrade to
    dynamic thinking, not propagate out of a probe."""
    budgets({"relevance": 0})
    assert resolve_thinking_budget("relevance", "some/unknown-model") is None


@pytest.mark.parametrize("value", ["junk", None, -5, [1]])
def test_malformed_budgets_degrade_to_dynamic(budgets, value):
    budgets({"relevance": value})
    assert resolve_thinking_budget("relevance", GEMINI) is None


# --- wiring ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_budget_is_resolved_per_attempt_not_once(budgets):
    """The fallback model is a DIFFERENT model and may not support
    reasoning. Carrying a `thinking` kwarg computed for the primary into
    the fallback turns the resilience path into a hard 400 — a failure
    that only appears when the primary is already down.
    """
    budgets({"relevance": 0})
    seen: list[dict] = []

    class _Resp:
        choices = [type("c", (), {"message": type("m", (), {"content": "ok"})()})()]
        usage = type("u", (), {"prompt_tokens": 1, "completion_tokens": 1})()

    async def fake_acompletion(**kwargs):
        seen.append(kwargs)
        return _Resp()

    from contextedge.ai import provider

    with (
        patch.object(provider.litellm, "acompletion", side_effect=fake_acompletion),
        patch.object(provider, "record_llm_usage", lambda *a, **k: None),
    ):
        await provider.llm_complete(
            "hi",
            task="classification",
            model=GEMINI,
            prompt_name="relevance",
            prompt_version="v1",
        )

    assert seen
    assert seen[0]["thinking"] == {"type": "enabled", "budget_tokens": 0}


@pytest.mark.asyncio
async def test_no_thinking_kwarg_when_unconfigured(budgets):
    budgets({})
    seen: list[dict] = []

    class _Resp:
        choices = [type("c", (), {"message": type("m", (), {"content": "ok"})()})()]
        usage = type("u", (), {"prompt_tokens": 1, "completion_tokens": 1})()

    async def fake_acompletion(**kwargs):
        seen.append(kwargs)
        return _Resp()

    from contextedge.ai import provider

    with (
        patch.object(provider.litellm, "acompletion", side_effect=fake_acompletion),
        patch.object(provider, "record_llm_usage", lambda *a, **k: None),
    ):
        await provider.llm_complete(
            "hi", task="classification", model=GEMINI, prompt_name="relevance"
        )

    assert "thinking" not in seen[0]
