"""Applicability gate: ratio scoring, key-aware env, contradicted drops."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from contextedge.services.case_frame_service import build_case_frame
from contextedge.services.playbook_applicability import evaluate_trigger_conditions


def test_single_vague_trigger_is_strong_not_exact():
    version = SimpleNamespace(
        trigger_conditions={"symptoms": ["vpn login failing"]},
        conflicts=None,
    )
    frame = build_case_frame(symptoms=["users report vpn login failing"])
    verdict = evaluate_trigger_conditions(version, frame)
    assert verdict.level == "strong"
    assert verdict.drop is False


def test_two_matching_triggers_are_exact():
    version = SimpleNamespace(
        trigger_conditions={"symptoms": ["vpn login failing", "certificate expired"]},
        conflicts=None,
    )
    frame = build_case_frame(
        symptoms=["vpn login failing because the certificate expired"]
    )
    verdict = evaluate_trigger_conditions(version, frame)
    assert verdict.level == "exact"
    assert verdict.drop is False


def test_precise_playbook_beats_absence_of_misses():
    version = SimpleNamespace(
        trigger_conditions={
            "symptoms": [
                "sharepoint excel export",
                "empty xlsx",
                "workflow timeout",
            ]
        },
        conflicts=None,
    )
    frame = build_case_frame(
        symptoms=["SharePoint excel export returns an empty xlsx"]
    )
    verdict = evaluate_trigger_conditions(version, frame)
    assert verdict.level in {"strong", "partial"}
    assert "workflow timeout" in " ".join(verdict.differences)


def test_environment_mismatch_is_contradicted():
    version = SimpleNamespace(
        trigger_conditions={"requires": {"os": "windows"}},
        conflicts=None,
    )
    frame = build_case_frame(
        symptoms=["agent not working"],
        environment={"os": "linux"},
    )
    verdict = evaluate_trigger_conditions(version, frame)
    assert verdict.level == "contradicted"
    assert verdict.drop is True
    assert verdict.drop_reason == "environment_mismatch"


def test_excludes_condition_drops_matching_environment():
    version = SimpleNamespace(
        trigger_conditions={"excludes": {"os": "linux"}},
        conflicts=None,
    )
    frame = build_case_frame(symptoms=["queue stuck"], environment={"os": "linux"})
    verdict = evaluate_trigger_conditions(version, frame)
    assert verdict.drop is True
    assert verdict.level == "contradicted"


def test_key_aware_environment_match_is_strong():
    version = SimpleNamespace(
        trigger_conditions={"os": "windows"},
        conflicts=None,
    )
    frame = build_case_frame(
        symptoms=["agent crash"],
        environment={"os": "windows"},
    )
    verdict = evaluate_trigger_conditions(version, frame)
    assert verdict.level == "strong"
    assert verdict.drop is False


def test_expired_playbook_is_dropped():
    version = SimpleNamespace(trigger_conditions={}, conflicts=None)
    playbook = SimpleNamespace(expiry_at=datetime.now(UTC) - timedelta(days=1))
    frame = build_case_frame(symptoms=["x"])
    verdict = evaluate_trigger_conditions(version, frame, playbook=playbook)
    assert verdict.drop is True
    assert verdict.drop_reason == "expired"
