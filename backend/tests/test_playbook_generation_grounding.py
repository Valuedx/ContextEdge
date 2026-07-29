"""Generated playbooks must carry evidence provenance and policy-derived risk."""

from contextedge.workers.pattern_tasks import _effective_risk_tier


def test_risk_floor_from_destructive_step_overrides_llm_low():
    steps = [{"title": "reimage", "safety_class": "destructive"}]
    assert _effective_risk_tier("low", steps) == "high"


def test_llm_may_raise_risk_above_floor():
    steps = [{"title": "check status", "safety_class": "read_only"}]
    assert _effective_risk_tier("high", steps) == "high"


def test_unknown_llm_tier_falls_back_to_floor_min_medium():
    steps = [{"title": "check status", "safety_class": "read_only"}]
    assert _effective_risk_tier("catastrophic", steps) == "medium"
    assert _effective_risk_tier(None, steps) == "medium"


def test_unknown_step_safety_class_floors_high():
    steps = [{"title": "mystery", "safety_class": "tpyo"}]
    assert _effective_risk_tier("low", steps) == "high"


def test_low_side_effect_floors_medium():
    steps = [{"title": "restart worker", "safety_class": "low_side_effect"}]
    assert _effective_risk_tier("low", steps) == "medium"
