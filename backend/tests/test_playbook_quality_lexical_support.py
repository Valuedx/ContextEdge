"""Stage C lexical support validator and semantic_match helpers."""

from __future__ import annotations

from contextedge.quality.registry import ValidationContext
from contextedge.quality.semantic_match import (
    best_support_score,
    combined_entailment_score,
    contract_source_claims,
    contradicts_negative,
)
from contextedge.quality.validators.lexical_support import validate

RESTART = "Restart the widget service on the affected host."
FORBID = "Do not restart the widget service on the affected host."


def test_contract_claims_extract_obligations():
    claims = contract_source_claims(
        {
            "required_actions": ["Restart the widget service on host-01"],
            "known_failed_actions": ["Delete the config file manually"],
        }
    )
    texts = [text for text, _ in claims]
    assert "Restart the widget service on host-01" in texts


def test_combined_score_catches_reordered_paraphrase():
    source = "Restart the widget service on the affected host"
    step = "On the affected host restart the widget service"
    assert combined_entailment_score(step, source) >= 0.25


def test_lexical_support_flags_unsupported_grounded_step():
    ctx = ValidationContext(
        content={
            "title": "Widget outage",
            "steps": [
                {
                    "step_id": "s1",
                    "grounding_status": "grounded",
                    "source_refs": ["kb-1"],
                    "text": "Deploy lunar rover firmware update sequence",
                }
            ],
        },
        content_hash="x",
        playbook_id="p1",
        tenant_id="t1",
        contract={
            "required_actions": ["Restart the widget service on host-01"],
        },
    )
    result = validate(ctx)
    assert result.dimension_states["evidence_grounding"] == "fail"
    assert any(f.severity == "major" for f in result.findings)


def test_lexical_support_passes_faithful_paraphrase():
    action = "Restart the widget service on host-01"
    ctx = ValidationContext(
        content={
            "title": "Widget outage",
            "steps": [
                {
                    "step_id": "s1",
                    "grounding_status": "grounded",
                    "source_refs": ["kb-1"],
                    "text": action,
                }
            ],
        },
        content_hash="x",
        playbook_id="p1",
        tenant_id="t1",
        contract={"required_actions": [action]},
    )
    result = validate(ctx)
    assert result.dimension_states["evidence_grounding"] == "pass"


def test_lexical_support_flags_polarity_conflict():
    ctx = ValidationContext(
        content={
            "title": "Widget outage",
            "steps": [
                {
                    "step_id": "s1",
                    "grounding_status": "grounded",
                    "source_refs": ["kb-1"],
                    "text": RESTART,
                }
            ],
        },
        content_hash="x",
        playbook_id="p1",
        tenant_id="t1",
        contract={"required_actions": [FORBID]},
    )
    result = validate(ctx)
    assert result.dimension_states["evidence_grounding"] == "fail"
    assert any(
        f.category == "contradicted_claim" and f.severity == "major"
        for f in result.findings
    )


def test_contradicts_negative_detects_known_failed_action():
    neg = "Manually delete the config file from /etc/widget"
    step = "Manually delete the config file from /etc/widget/app.conf"
    score, matched = contradicts_negative(step, [neg])
    assert score >= 0.32
    assert matched == neg


def test_best_support_finds_claim():
    score, matched = best_support_score(
        "Restart the widget service on host-01",
        [("Restart the widget service on host-01", "required_actions")],
    )
    assert score >= 0.9
    assert matched is not None
