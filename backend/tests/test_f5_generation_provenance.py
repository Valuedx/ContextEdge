"""F5 — a generated artifact records what generated it.

Prompt and model versions reached the cost plane and stopped there, so "which
prompt version wrote this episode?" needed a correlation-id join that only
worked when the caller happened to hold a db session. These tests pin the
stamp, the honesty of its field names, and the two cases that must stay NULL:
a hand-authored playbook version and a pattern created without synthesis.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from contextedge.ai.provenance import GENERATION_PROVENANCE_KEY, generation_provenance

_PROMPT = SimpleNamespace(name="episode", version="v3", system="s", user_template="{x}")


def test_provenance_records_the_generating_configuration():
    with patch("contextedge.ai.provider.get_model_for_task", return_value="vertex/gemini"):
        prov = generation_provenance(_PROMPT, task="extraction")
    assert prov["prompt_name"] == "episode"
    assert prov["prompt_version"] == "v3"
    assert prov["task"] == "extraction"
    assert prov["model_requested"] == "vertex/gemini"


def test_the_model_field_is_named_for_what_it_actually_knows():
    """E1's breaker can substitute settings.llm_fallback_model mid-call and
    only llm.usage sees that. Calling this field ``model`` would be a quiet
    lie, so it is ``model_requested`` and correlation_id is the join to the
    event that knows the serving model."""
    prov = generation_provenance(_PROMPT, task="extraction", model_requested="m")
    assert "model" not in prov
    assert prov["model_requested"] == "m"
    assert "correlation_id" in prov


def test_provenance_degrades_instead_of_raising_on_a_promptless_call():
    prov = generation_provenance(object(), task="extraction", model_requested="m")
    assert prov["prompt_name"] is None
    assert prov["prompt_version"] is None


def test_correlation_id_is_captured_when_a_request_context_exists():
    marker = uuid4()
    with patch(
        "contextedge.middleware.request_context.current_correlation_id",
        return_value=marker,
    ):
        prov = generation_provenance(_PROMPT, task="extraction", model_requested="m")
    assert prov["correlation_id"] == str(marker)


def test_correlation_id_is_null_outside_a_request_context():
    with patch(
        "contextedge.middleware.request_context.current_correlation_id",
        return_value=None,
    ):
        prov = generation_provenance(_PROMPT, task="extraction", model_requested="m")
    assert prov["correlation_id"] is None


@pytest.mark.asyncio
async def test_episode_extraction_stamps_provenance_after_the_schema_gate():
    """Stamped by the caller, so the model can neither supply nor influence
    its own provenance — a model-supplied key would be overwritten."""
    from contextedge.ai.extractors import episode_extractor

    model_output = {
        "episodes": [
            {
                "title": "VPN auth failures after KB5032190",
                "root_cause_summary": "Gateway certificate expired",
                "overall_confidence": 0.8,
                "steps": [],
                "evidence_refs": ["ev-1"],
                GENERATION_PROVENANCE_KEY: {"prompt_version": "attacker-supplied"},
            }
        ]
    }
    with (
        patch.object(
            episode_extractor, "llm_complete_json", AsyncMock(return_value=model_output)
        ),
        patch.object(
            episode_extractor,
            "get_prompt",
            return_value=SimpleNamespace(
                name="episode", version="v3", system="s", format_user=lambda **_: "u"
            ),
        ),
        patch("contextedge.ai.provider.get_model_for_task", return_value="vertex/gemini"),
    ):
        episodes = await episode_extractor._extract_from_chunk(
            [{"evidence_id": str(uuid4()), "title": "t", "body": "b"}]
        )

    assert len(episodes) == 1
    prov = episodes[0][GENERATION_PROVENANCE_KEY]
    assert prov["prompt_version"] == "v3", "the caller's stamp must win"
    assert prov["prompt_name"] == "episode"


@pytest.mark.asyncio
async def test_pattern_synthesis_stamps_provenance_only_on_a_dict_result():
    from contextedge.ai.extractors import pattern_extractor

    prompt = SimpleNamespace(
        name="pattern", version="v2", system="s", format_user=lambda **_: "u"
    )
    with (
        patch.object(pattern_extractor, "get_prompt", return_value=prompt),
        patch("contextedge.ai.provider.get_model_for_task", return_value="vertex/gemini"),
    ):
        with patch.object(
            pattern_extractor, "llm_complete_json", AsyncMock(return_value={"title": "t"})
        ):
            result = await pattern_extractor.synthesize_pattern([{"title": "e"}])
        assert result[GENERATION_PROVENANCE_KEY]["prompt_version"] == "v2"

        # A salvaged/garbled response is not a dict; it must degrade to
        # "unknown provenance", not crash on the way to the pattern writer.
        with patch.object(
            pattern_extractor, "llm_complete_json", AsyncMock(return_value=["junk"])
        ):
            assert await pattern_extractor.synthesize_pattern([{"title": "e"}]) == ["junk"]


def test_only_generated_artifacts_carry_the_column():
    """Decisions and claims are written by services and humans, not by a
    prompt. A column that is NULL by construction is the schema-claims-
    capability problem this epic exists to stop."""
    from contextedge.models.decision import Decision
    from contextedge.models.episode import Episode
    from contextedge.models.pattern import Pattern
    from contextedge.models.playbook import PlaybookVersion

    for model in (Episode, Pattern, PlaybookVersion):
        assert hasattr(model, "generation_provenance"), model.__name__
    assert not hasattr(Decision, "generation_provenance")


@pytest.mark.asyncio
async def test_playbook_generation_stamps_provenance():
    from contextedge.ai.generators import playbook_generator

    prompt = SimpleNamespace(
        name="playbook",
        version="v2",
        system="s",
        user_template="{pattern_title}",
        format_user=lambda **_: "u",
    )
    with (
        patch.object(playbook_generator, "get_prompt", return_value=prompt),
        patch.object(
            playbook_generator, "llm_complete_json", AsyncMock(return_value={"steps": []})
        ),
        patch.object(playbook_generator, "validate_source_refs", lambda *_a, **_k: None),
        patch.object(playbook_generator, "classify_step_grounding", lambda *_a, **_k: None),
        patch("contextedge.ai.provider.get_model_for_task", return_value="vertex/gemini"),
    ):
        result = await playbook_generator.generate_playbook_candidate(
            pattern_title="VPN auth failures after KB5032190",
            pattern_description="Gateway certificate expired",
            episode_count=2,
            episode_summaries=[],
            negative_knowledge=[],
        )
    assert result[GENERATION_PROVENANCE_KEY]["prompt_version"] == "v2"
    assert result[GENERATION_PROVENANCE_KEY]["task"] == "playbook"


@pytest.mark.asyncio
async def test_create_playbook_version_persists_a_forwarded_stamp():
    """The worker assembles version_data field by field, so the stamp must be
    forwarded explicitly — unlike the API path, which passes the candidate
    dict whole. This is the wiring that silently produced NULL during F5's
    own review pass, so both ends are pinned."""
    from contextedge.services import playbook_service

    captured: list = []

    class _Nested:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

    db = SimpleNamespace(
        add=captured.append,
        flush=AsyncMock(),
        refresh=AsyncMock(),
        execute=AsyncMock(),
        begin_nested=lambda: _Nested(),
    )
    playbook = SimpleNamespace(id=uuid4(), tenant_id=uuid4(), domain_id=None)
    stamp = {"prompt_name": "playbook", "prompt_version": "v2"}

    with (
        patch.object(
            playbook_service, "_existing_semantic_versions", AsyncMock(return_value=set())
        ),
        patch.object(playbook_service, "append_operational_event", AsyncMock()),
    ):
        version = await playbook_service.create_playbook_version(
            db, playbook, {"steps": [], GENERATION_PROVENANCE_KEY: stamp}
        )
    assert version.generation_provenance == stamp


def test_hand_authored_playbook_version_has_no_provenance():
    """create_playbook_version reads the key out of version_data; an
    author-supplied payload simply does not have it."""
    from contextedge.models.playbook import PlaybookVersion

    version = PlaybookVersion(
        playbook_id=uuid4(),
        semantic_version="1.0.0",
        steps=[{"title": "Renew the gateway certificate"}],
    )
    assert version.generation_provenance is None
