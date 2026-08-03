"""Extracted aliases, and the cache marker that silently killed calls.

Two findings from looking at a live tenant's 181 extracted identities:

`SFA` and `Sales Force Automation` exist as separate rows, as do
`HP UPD` and `HP Universal Print Driver`. Candidate generation matches on
shared substrings, so the adjudicator was never even shown either pair —
92% of mentions never reached it at all. When the content itself states
the relationship, extraction is the place to capture it.

And while measuring that, every call with the new prompt failed: above
roughly 3k characters of system prompt, LiteLLM turns a `cache_control`
marker into a Vertex context-cache resource that 404s. The call does not
degrade, it fails, and the error reads like a provider outage.
"""

from __future__ import annotations

import pytest

from contextedge.ai.observability import build_messages
from contextedge.ai.provider import supports_explicit_cache_control
from contextedge.services.identity_normalizer import normalize_extracted_entity


# --- extracted aliases --------------------------------------------------------


def test_a_stated_abbreviation_is_carried_as_an_alias():
    entity = normalize_extracted_entity(
        {
            "entity_type": "application",
            "display_name": "Sales Force Automation",
            "aliases": ["SFA"],
        }
    )
    assert entity.display_name == "Sales Force Automation"
    assert entity.aliases == ["SFA"]


def test_an_alias_equal_to_the_display_name_teaches_nothing():
    entity = normalize_extracted_entity(
        {
            "entity_type": "application",
            "display_name": "Print Spooler",
            "aliases": ["print spooler", "PRINT SPOOLER", "Spooler"],
        }
    )
    assert entity.aliases == ["Spooler"]


def test_a_single_character_alias_is_refused():
    """It would match far too much to be worth learning."""
    entity = normalize_extracted_entity(
        {"entity_type": "application", "display_name": "Some Product", "aliases": ["S"]}
    )
    assert entity.aliases == []


def test_duplicate_aliases_are_collapsed():
    entity = normalize_extracted_entity(
        {
            "entity_type": "application",
            "display_name": "Field Dispatch Platform",
            "aliases": ["FDP", "FDP"],
        }
    )
    assert entity.aliases == ["FDP"]


def test_absent_aliases_are_not_an_error():
    """v1 and v2 of the prompt emit no aliases key at all."""
    entity = normalize_extracted_entity(
        {"entity_type": "device", "display_name": "edge-gw-01"}
    )
    assert entity.aliases == []


def test_aliases_do_not_disturb_strong_identifiers():
    entity = normalize_extracted_entity(
        {
            "entity_type": "person",
            "display_name": "J. Smith",
            "email": "jsmith@example.com",
            "aliases": ["JS"],
        }
    )
    assert entity.aliases == ["JS"]
    assert ("email", "jsmith@example.com", None) in entity.strong_identifiers


def test_extracted_aliases_are_learned_at_identity_creation():
    """Creation is the moment that matters — this is the identity a later
    abbreviation would otherwise fork away from."""
    import inspect

    from contextedge.services import identity_service

    source = inspect.getsource(identity_service._create_identity)
    assert "_learn_content_aliases" in source


# --- the cache marker ---------------------------------------------------------


def test_vertex_does_not_receive_an_explicit_cache_marker():
    """The marker was written for Anthropic and assumed harmless
    elsewhere. On Vertex it is not: LiteLLM translates it into a context
    cache resource whose creation 404s, and the whole call fails."""
    assert supports_explicit_cache_control("vertex_ai/gemini-2.5-flash") is False
    assert supports_explicit_cache_control("gemini/gemini-2.0-flash") is False


def test_providers_with_explicit_caching_still_receive_it():
    for model in (
        "anthropic/claude-sonnet-4",
        "claude-3-5-haiku",
        "openai/gpt-4o",
        "gpt-4o-mini",
        "azure/gpt-4o",
    ):
        assert supports_explicit_cache_control(model) is True


def test_an_unknown_model_is_treated_as_not_supporting_it():
    """Fail safe: omitting the marker costs a cache hit, sending it to a
    provider that mishandles it costs the entire call."""
    assert supports_explicit_cache_control("some-new-provider/model") is False
    assert supports_explicit_cache_control(None) is False


@pytest.mark.parametrize("cache", [True, False])
def test_the_system_prompt_survives_either_way(cache):
    """Whatever the caching decision, the instructions must still reach
    the model — the fix must not quietly drop the system block."""
    messages = build_messages("SYSTEM TEXT", "user text", cache_system=cache)
    rendered = str(messages)
    assert "SYSTEM TEXT" in rendered
    assert "user text" in rendered
    assert ("cache_control" in rendered) is cache
