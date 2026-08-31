"""Grounded vs best-practice playbook steps: the contract is structural.

A step's classification derives from its VALIDATED citations, never
from what the model claims — including the downgrade case where minted
citations were dropped and an allegedly grounded step is left bare.
"""

from __future__ import annotations

from types import SimpleNamespace

from contextedge.ai.generators.playbook_generator import (
    BEST_PRACTICE_REASON,
    classify_step_grounding,
)
from contextedge.graph.agent.hydrators import playbook_version_facts


def test_sourced_step_is_grounded_and_bare_step_is_forced_best_practice():
    result = {
        "steps": [
            {"text": "Restart the iDoc queue", "source_refs": [{"label": "kb-1", "id": "x"}]},
            # Model claimed grounded; its minted citation was dropped by
            # validate_source_refs — the empty list downgrades it.
            {"text": "Verify checksum of the driver package", "source_refs": [],
             "grounding_status": "grounded"},
        ]
    }
    counts = classify_step_grounding(result)
    grounded, bp = result["steps"]
    assert grounded["grounding_status"] == "grounded"
    assert grounded["step_classification"] == "procedure"
    assert bp["grounding_status"] == "non_grounded"
    assert bp["step_classification"] == "best_practice"
    assert bp["confidence"] == "best_practice"
    assert bp["reason"] == BEST_PRACTICE_REASON
    assert counts == {"grounded": 1, "best_practice": 1}


def test_best_practice_steps_never_raise_the_grounded_ratio():
    base = {"steps": [{"text": "a", "source_refs": [{"id": "1"}]}]}
    classify_step_grounding(base)
    ratio_before = base["grounding"]["grounded_ratio"]
    padded = {
        "steps": [
            {"text": "a", "source_refs": [{"id": "1"}]},
            {"text": "backup first", "source_refs": []},
            {"text": "health check after", "source_refs": []},
        ]
    }
    classify_step_grounding(padded)
    assert padded["grounding"]["grounded_ratio"] < ratio_before
    assert padded["grounding"]["best_practice"] == 2


def test_model_may_not_dodge_review_by_mislabeling_evidenced_steps():
    result = {"steps": [{"text": "x", "source_refs": [{"id": "1"}],
                         "grounding_status": "non_grounded",
                         "step_classification": "best_practice"}]}
    classify_step_grounding(result)
    assert result["steps"][0]["grounding_status"] == "grounded"


def test_projection_marks_best_practice_steps():
    version = SimpleNamespace(
        semantic_version="1.0.0",
        steps=[
            {"text": "Restart the queue", "grounding_status": "grounded"},
            {"text": "Take a backup first", "grounding_status": "non_grounded"},
        ],
        trigger_conditions=None,
        rollback_notes=None,
        playbook_confidence=None,
    )
    facts, _ = playbook_version_facts(version)
    assert facts["steps"][0] == "1. Restart the queue"
    assert facts["steps"][1].startswith("2. [best practice] ")


def test_prompt_v9_is_default_and_earlier_versions_untouched():
    """v9 took the default so mail-thread solutions under each episode
    are used together with KB. The grounded / best-practice taxonomy
    this module covers came in at v5 and must survive into whatever is
    current — it is inherited, not re-stated.
    """
    from contextedge.ai import prompts as prompts_mod

    assert prompts_mod._DEFAULTS["playbook"] == "v9"
    for superseded in ("v4", "v5", "v6", "v7", "v8"):
        assert superseded in prompts_mod._REGISTRY["playbook"]
    current = prompts_mod._REGISTRY["playbook"]["v9"].system
    assert "non_grounded" in current and "best_practice" in current
    assert "KB sections as a coverage checklist" in current
    assert "PRODUCT VERSION MISMATCH" in current
    assert "Use BOTH sources" in current
