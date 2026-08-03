"""A1 message-function classifier: prompt, vocabulary guard, veto policy."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from contextedge.ai.classifiers.message_function import classify_message_function
from contextedge.ai.prompts.message_function import MESSAGE_FUNCTIONS
from contextedge.services.ticket_bridge_service import is_dissociative


def _ev(body, title=None, function=None, confidence=None):
    return SimpleNamespace(
        body_text=body,
        title=title,
        message_function=function,
        message_function_confidence=confidence,
    )


# --- classifier -------------------------------------------------------------


@pytest.mark.asyncio
async def test_classifier_returns_vocabulary_label():
    with patch(
        "contextedge.ai.classifiers.message_function.llm_complete_json",
        AsyncMock(return_value={"function": "dissociation", "confidence": 0.92}),
    ):
        out = await classify_message_function(
            "", "This isn't about the VPN thing", "teams"
        )
    assert out == {"function": "dissociation", "confidence": 0.92}


@pytest.mark.asyncio
async def test_out_of_vocabulary_label_degrades_to_unclassified():
    with patch(
        "contextedge.ai.classifiers.message_function.llm_complete_json",
        AsyncMock(return_value={"function": "sarcasm", "confidence": "high"}),
    ):
        out = await classify_message_function("", "hmm", "teams")
    assert out["function"] == "unclassified"
    assert out["confidence"] == 0.0


def test_prompt_vocabulary_matches_registry():
    from contextedge.ai.prompts import get_prompt

    prompt = get_prompt("message_function")
    assert prompt.version == "v1"
    for label in MESSAGE_FUNCTIONS:
        assert label in prompt.system


# --- veto policy ------------------------------------------------------------


def test_confident_dissociation_label_vetoes_paraphrase():
    """The phrase list can't see this wording; the classifier can."""
    ev = _ev("this isn't about the VPN thing", function="dissociation", confidence=0.9)
    assert is_dissociative(ev) is True


def test_dissociation_label_vetoes_even_at_low_confidence():
    ev = _ev("maybe separate?", function="dissociation", confidence=0.3)
    assert is_dissociative(ev) is True


def test_unlabeled_falls_back_to_phrase_floor():
    """LLM budget exhausted / pre-0041 rows: phrase list still vetoes."""
    assert is_dissociative(_ev("different issue, is the DB also down?")) is True
    assert is_dissociative(_ev("has it recovered?")) is False


def test_confident_other_label_rescues_false_phrase_hit():
    """'not related' inside an explanation is not a dissociation."""
    ev = _ev(
        "the outage is not related to load, it's the expired certificate",
        function="status_update",
        confidence=0.85,
    )
    assert is_dissociative(ev) is False


def test_low_confidence_other_label_defers_to_phrase_floor():
    ev = _ev("different issue entirely", function="status_update", confidence=0.4)
    assert is_dissociative(ev) is True


def test_correction_label_also_abstains_from_inheritance():
    """A correction changes what earlier messages established; inheriting
    the parent's case before A2 resolves it would link the wrong case."""
    ev = _ev(
        "Correction - it is Mary's ticket, not John's",
        function="correction",
        confidence=0.9,
    )
    assert is_dissociative(ev) is True
