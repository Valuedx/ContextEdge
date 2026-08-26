from contextedge.search.fusion import DEFAULT_ARM_WEIGHTS, rrf_max, rrf_scores
from contextedge.services.case_frame_service import build_case_frame
from contextedge.services.playbook_applicability import evaluate_trigger_conditions
from types import SimpleNamespace
from uuid import uuid4


def test_case_frame_splits_symptom_and_lexical_terms():
    frame = build_case_frame(
        symptoms=["VPN login failing AUTH_CERT_EXPIRED"],
        entities=["vpn-gw-east-01"],
        environment={"os": "windows"},
    )
    assert "VPN login failing" in frame.symptom_text
    assert any("vpn" in t for t in frame.lexical_terms)
    assert frame.environment["os"] == "windows"


def test_case_frame_empty_inputs_are_safe():
    frame = build_case_frame()
    assert frame.symptom_text == ""
    assert frame.lexical_terms == []


def test_rrf_is_rank_based_not_score_based():
    a, b = uuid4(), uuid4()
    scores = rrf_scores({"r1_embedding": [a, b], "r2_lexical": [b]})
    assert scores[a] > 0
    assert scores[b] > scores[a]
    assert rrf_max() == sum(w / 61 for w in DEFAULT_ARM_WEIGHTS.values())


def test_applicability_two_matching_triggers_are_exact():
    version = SimpleNamespace(
        trigger_conditions={"symptoms": ["vpn login failing", "auth cert expired"]},
        conflicts=None,
    )
    frame = build_case_frame(symptoms=["users report vpn login failing AUTH_CERT_EXPIRED"])
    verdict = evaluate_trigger_conditions(version, frame)
    assert verdict.level == "exact"
    assert verdict.drop is False


def test_applicability_expired_playbook_is_dropped():
    from datetime import UTC, datetime, timedelta

    version = SimpleNamespace(trigger_conditions={}, conflicts=None)
    playbook = SimpleNamespace(expiry_at=datetime.now(UTC) - timedelta(days=1))
    frame = build_case_frame(symptoms=["x"])
    verdict = evaluate_trigger_conditions(version, frame, playbook=playbook)
    assert verdict.drop is True
    assert verdict.drop_reason == "expired"
