"""P4 source-aware synthesis: tenant-configurable roles, strict draft
schema, contradiction preservation, prompt v3."""

from uuid import uuid4

import pytest

from contextedge.ai.extractors.episode_schema import validate_episode
from contextedge.workers.extraction_tasks import (
    SOURCE_ROLE_MAP,
    SYNTHESIS_ROLES,
    resolve_synthesis_role,
)


# --- role resolution --------------------------------------------------------


def test_default_roles_unchanged():
    assert resolve_synthesis_role("servicenow", None) == "ticket"
    assert resolve_synthesis_role("teams", {}) == "working_discussion"
    assert resolve_synthesis_role("unknown_type", None) == "evidence"


def test_source_config_overrides_role():
    """A Teams channel receiving alert webhooks is really a monitoring feed."""
    assert (
        resolve_synthesis_role("teams", {"synthesis_role": "monitoring"})
        == "monitoring"
    )


def test_invalid_override_falls_back_to_default():
    """A config typo degrades to today's behavior, never poisons authority."""
    assert resolve_synthesis_role("teams", {"synthesis_role": "boss"}) == (
        "working_discussion"
    )
    assert resolve_synthesis_role("gmail", {"synthesis_role": 42}) == (
        "external_communication"
    )


def test_role_vocabulary_covers_defaults():
    for role in SOURCE_ROLE_MAP.values():
        assert role in SYNTHESIS_ROLES


# --- strict draft schema ----------------------------------------------------


def test_valid_episode_passes_and_clamps():
    ep = validate_episode(
        {
            "title": "VPN outage",
            "overall_confidence": 1.7,  # out of range → clamp
            "steps": [
                {
                    "step_order": 1,
                    "step_type": "made_up_type",  # unknown → observation
                    "text": "Renewed certificate",
                    "result_state": "great",  # unknown → unknown
                    "confidence": -0.2,  # clamp
                }
            ],
        }
    )
    assert ep is not None
    assert ep["overall_confidence"] == 1.0
    assert ep["steps"][0]["step_type"] == "observation"
    assert ep["steps"][0]["result_state"] == "unknown"
    assert ep["steps"][0]["confidence"] == 0.0


def test_structurally_broken_episode_is_dropped():
    assert validate_episode({"title": ""}) is None  # empty title
    assert validate_episode({"steps": []}) is None  # no title at all


def test_bad_step_drops_only_the_step():
    ep = validate_episode(
        {
            "title": "VPN outage",
            "steps": [
                {"step_order": 1, "step_type": "action", "text": "Renewed cert"},
                {"step_order": 2, "text": ""},  # empty text → step dropped
                "garbage",
            ],
        }
    )
    assert ep is not None
    assert len(ep["steps"]) == 1
    assert ep["steps"][0]["text"] == "Renewed cert"


def test_malformed_contradiction_never_costs_the_episode():
    ep = validate_episode(
        {
            "title": "VPN outage",
            "contradictions": [
                {"topic": "resolution", "accounts": [{"claim": "only one side"}]},
                "garbage",
                {
                    "topic": "resolution",
                    "accounts": [
                        {"evidence_id": str(uuid4()), "claim": "cert renewed"},
                        {"evidence_id": str(uuid4()), "claim": "service rolled back"},
                    ],
                },
            ],
        }
    )
    assert ep is not None
    assert len(ep["contradictions"]) == 1  # only the well-formed one survives
    assert ep["contradictions"][0]["topic"] == "resolution"


# --- prompt v3 --------------------------------------------------------------


def test_episode_prompt_v3_is_default_with_authority_rules():
    from contextedge.ai.prompts import get_prompt

    prompt = get_prompt("episode")
    assert prompt.version == "v3"
    assert "Source authority by fact type" in prompt.system
    assert "contradictions" in prompt.system
    # v3 must keep the single-brace fix: the JSON example reaches the
    # model verbatim (system is never .format()ed).
    assert "{{" not in prompt.system
    # v1/v2 stay registered for eval baselines.
    from contextedge.ai.prompts import list_prompt_versions

    assert list_prompt_versions("episode") == ["v1", "v2", "v3"]


# --- extractor integration --------------------------------------------------


@pytest.mark.asyncio
async def test_extractor_translates_contradiction_refs_and_validates(monkeypatch):
    from contextedge.ai.extractors import episode_extractor

    ev_a, ev_b = str(uuid4()), str(uuid4())

    async def fake_llm(*args, **kwargs):
        return {
            "episodes": [
                {
                    "title": "VPN outage",
                    "overall_confidence": 0.9,
                    "evidence_refs": ["ev-1", "ev-2"],
                    "contradictions": [
                        {
                            "topic": "what fixed it",
                            "accounts": [
                                {"evidence_ref": "ev-1", "claim": "cert renewed"},
                                {"evidence_ref": "ev-9", "claim": "minted source"},
                            ],
                        }
                    ],
                    "steps": [
                        {
                            "step_order": 1,
                            "step_type": "action",
                            "text": "Renewed certificate",
                            "result_state": "success",
                            "confidence": 0.9,
                            "evidence_refs": ["ev-1"],
                        }
                    ],
                },
                {"title": ""},  # structurally broken → dropped
            ]
        }

    monkeypatch.setattr(episode_extractor, "llm_complete_json", fake_llm)

    episodes = await episode_extractor.reconstruct_episode(
        [
            {"title": "INC ticket", "body": "cert renewed", "evidence_id": ev_a},
            {"title": "Teams thread", "body": "rolled back", "evidence_id": ev_b},
        ]
    )

    assert len(episodes) == 1
    ep = episodes[0]
    assert ep["evidence_refs"] == [ev_a, ev_b]
    accounts = ep["contradictions"][0]["accounts"]
    assert accounts[0]["evidence_id"] == ev_a  # ev-1 translated
    assert accounts[1]["evidence_id"] is None  # ev-9 was minted → dropped
    assert ep["steps"][0]["evidence_refs"] == [ev_a]
