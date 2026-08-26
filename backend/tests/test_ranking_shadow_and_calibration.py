from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from contextedge.integrations.maf.playbook_client import normalize_playbook_steps
from contextedge.search.hybrid_ranker import RankedPlaybook, _legacy_linear_score, RankingWeights
from contextedge.services.score_calibration import CalibrationMap, calibrate_confidence


def test_normalize_playbook_steps_fills_governed_fields():
    steps = normalize_playbook_steps(
        [
            {
                "instruction": "Restart the service",
                "rollback": "start it again",
                "tool": "winrm",
                "safety_class": "low_side_effect",
            }
        ]
    )
    assert len(steps) == 1
    step = steps[0]
    assert step["title"] == "Restart the service"
    assert step["safety_class"] == "low_side_effect"
    assert step["requires_approval"] is False
    assert step["reversible"] is False
    assert step["rollback_hint"] == "start it again"
    assert step["verification"] is False
    assert step["tool_ref"] == "winrm"
    assert step["inputs"] == {}


def test_calibrate_confidence_pass_through_without_map():
    assert calibrate_confidence(0.42) == 0.42
    assert calibrate_confidence(1.7) == 1.0


def test_calibrate_confidence_applies_isotonic_points():
    mapping = CalibrationMap(
        isotonic_points=((0.0, 0.1), (0.5, 0.4), (1.0, 0.9)),
    )
    assert calibrate_confidence(0.0, mapping) == pytest.approx(0.1)
    assert calibrate_confidence(0.5, mapping) == pytest.approx(0.4)
    assert calibrate_confidence(0.75, mapping) == pytest.approx(0.65)


def test_legacy_linear_score_uses_ranking_weights():
    weights = RankingWeights()
    score = _legacy_linear_score(
        weights,
        keyword=1.0,
        semantic=1.0,
        graph=0.0,
        quality=0.5,
        identity_score=0.0,
        freshness=1.0,
        neg=0.0,
    )
    expected = 0.25 + 0.30 + 0.10 * 0.5 + 0.15
    assert score == pytest.approx(expected)


def test_bound_weights_clips_per_run_delta():
    from contextedge.workers.ranking_calibration_tasks import _bound_weights

    previous = {
        "r1_embedding": 1.0,
        "r2_lexical": 0.8,
        "r3_signature": 1.2,
        "r4_evidence": 0.6,
    }
    proposed = {
        "r1_embedding": 5.0,
        "r2_lexical": 0.0,
        "r3_signature": 1.3,
        "r4_evidence": 0.6,
    }
    bounded = _bound_weights(proposed, previous)
    assert bounded["r1_embedding"] == pytest.approx(1.25)
    assert bounded["r2_lexical"] == pytest.approx(0.55)
    assert bounded["r3_signature"] == pytest.approx(1.3)


@pytest.mark.asyncio
async def test_rank_playbooks_shadow_serves_linear_and_logs_fused():
    from contextedge.search.playbook_candidates import CandidateSet
    from contextedge.services.playbook_applicability import ApplicabilityVerdict
    from contextedge.search import hybrid_ranker

    tenant_id = uuid4()
    playbook_id = uuid4()
    playbook = SimpleNamespace(
        id=playbook_id,
        tenant_id=tenant_id,
        lifecycle_state="approved",
        domain_id=None,
        risk_tier="medium",
        expiry_at=None,
        last_validated_at=None,
        title="Restart DB",
        stable_key="restart-db",
        automation_mode="assisted",
    )
    version = SimpleNamespace(
        id=uuid4(),
        playbook_confidence=0.8,
        trigger_conditions={},
        conflicts=None,
        semantic_version="1.0.0",
    )
    candidates = CandidateSet(
        playbooks={playbook_id: playbook},
        arm_ranks={"r1_embedding": [playbook_id], "r2_lexical": [playbook_id]},
    )
    db = SimpleNamespace(execute=AsyncMock())

    with (
        patch.object(hybrid_ranker, "_shadow_mode", return_value=True),
        patch.object(
            hybrid_ranker, "load_active_calibration", AsyncMock(return_value=None)
        ),
        patch.object(
            hybrid_ranker,
            "generate_playbook_candidates",
            AsyncMock(return_value=candidates),
        ),
        patch.object(
            hybrid_ranker,
            "_latest_published_versions",
            AsyncMock(return_value={playbook_id: version}),
        ),
        patch.object(
            hybrid_ranker, "resolve_identity_ids_for_terms", AsyncMock(return_value=set())
        ),
        patch.object(
            hybrid_ranker, "_batch_graph_counts", AsyncMock(return_value={playbook_id: 0})
        ),
        patch.object(
            hybrid_ranker, "_batch_identity_hits", AsyncMock(return_value={playbook_id: 0})
        ),
        patch.object(
            hybrid_ranker,
            "_batch_contradiction_counts",
            AsyncMock(return_value={playbook_id: 0}),
        ),
        patch.object(
            hybrid_ranker,
            "_batch_precedent_counts",
            AsyncMock(return_value={playbook_id: 0}),
        ),
        patch.object(
            hybrid_ranker,
            "_batch_evidence_link_counts",
            AsyncMock(return_value={playbook_id: 0}),
        ),
        patch.object(
            hybrid_ranker,
            "evaluate_trigger_conditions",
            lambda *a, **k: ApplicabilityVerdict(level="unvalidated"),
        ),
    ):
        ranked = await hybrid_ranker.rank_playbooks(
            db,
            tenant_id=tenant_id,
            query_text="restart database",
            min_score=0.0,
        )

    assert len(ranked) == 1
    row = ranked[0]
    assert isinstance(row, RankedPlaybook)
    assert row.score == pytest.approx(row.linear_score)
    assert row.breakdown["shadow_served"] == "linear"
    assert "fused_score" in row.breakdown
    assert row.confidence_calibrated == pytest.approx(row.breakdown["fused_score"])


def test_fused_weights_sum_to_one():
    from contextedge.search.hybrid_ranker import (
        FUSED_APPLY,
        FUSED_FRESHNESS,
        FUSED_IDENTITY,
        FUSED_PRECEDENT,
        FUSED_QUALITY,
        FUSED_RRF,
    )

    assert (
        FUSED_RRF
        + FUSED_QUALITY
        + FUSED_FRESHNESS
        + FUSED_APPLY
        + FUSED_PRECEDENT
        + FUSED_IDENTITY
    ) == pytest.approx(1.0)
