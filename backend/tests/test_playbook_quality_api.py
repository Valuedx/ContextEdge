"""HTTP handler tests for playbook quality read routes."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from contextedge.api.v1 import playbooks
from contextedge.quality import build_content
from contextedge.quality.hashing import content_hash
from contextedge.schemas.playbook import PlaybookQualitySummary
from tests.conftest import make_user


class _ScalarOneOrNoneResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _ScalarsResult:
    def __init__(self, values):
        self._values = values

    def scalars(self):
        return SimpleNamespace(all=lambda: self._values)

    def all(self):
        return self._values


def _summary(**over) -> dict:
    base = {
        "state": "inconclusive",
        "structure": "pass",
        "groups": {"subject": "inconclusive", "steps": "inconclusive", "coherence": None},
        "coverage": {"decided": 3, "undecided": 11, "total": 14},
        "finding_counts": {"critical": 0, "major": 1, "minor": 0, "info": 2},
        "matches_current_content": True,
        "assessed_at": datetime.now(UTC).isoformat(),
        "stale_reason": None,
        "evaluation_mode": "shadow",
    }
    base.update(over)
    return base


@pytest.mark.asyncio
async def test_get_playbook_quality_returns_report_shape():
    tenant_id = uuid4()
    playbook_id = uuid4()
    playbook = SimpleNamespace(id=playbook_id, tenant_id=tenant_id, current_version_id=uuid4())
    assessment = SimpleNamespace(
        id=uuid4(),
        content_revision_id=uuid4(),
        content_hash="a" * 64,
        validator_bundle_version="qa-2026.09.01",
        dimension_states={},
        started_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
        stale_at=None,
        superseded_at=None,
    )
    report = {
        "content_hash": "b" * 64,
        "assessment": assessment,
        "summary": _summary(),
        "findings": [
            SimpleNamespace(
                id=uuid4(),
                category="stale_grounding",
                dimension="evidence_grounding",
                severity="major",
                target_kind="step",
                target_ref="s1",
                claim=None,
                explanation="edited step kept citations",
                supporting_spans=[],
                contradicting_spans=[],
                validator="grounding_integrity",
                confidence=None,
                remediation_category=None,
                created_at=datetime.now(UTC),
            )
        ],
        "readiness": {
            "ready": False,
            "state": "inconclusive",
            "blocked_reason": "assessment_inconclusive",
            "content_hash": "b" * 64,
        },
    }

    with patch.object(
        playbooks,
        "_load_tenant_playbook",
        AsyncMock(return_value=playbook),
    ), patch(
        "contextedge.services.playbook_quality_service.quality_report",
        AsyncMock(return_value=report),
    ) as quality_report:
        response = await playbooks.get_playbook_quality(
            playbook_id=playbook_id,
            db=SimpleNamespace(),
            user=make_user(tenant_id=tenant_id),
        )

    quality_report.assert_awaited_once()
    assert response.playbook_id == playbook_id
    assert response.content_hash == report["content_hash"]
    assert response.summary.coverage.total == 14
    assert response.summary.groups.steps == "inconclusive"
    assert response.readiness.ready is False
    assert len(response.findings) == 1
    assert response.findings[0].target_ref == "s1"


@pytest.mark.asyncio
async def test_get_playbook_quality_is_read_only():
    tenant_id = uuid4()
    playbook_id = uuid4()
    playbook = SimpleNamespace(id=playbook_id, tenant_id=tenant_id, current_version_id=None)

    with patch.object(
        playbooks,
        "_load_tenant_playbook",
        AsyncMock(return_value=playbook),
    ), patch(
        "contextedge.services.playbook_quality_service.quality_report",
        AsyncMock(
            return_value={
                "content_hash": "a" * 64,
                "assessment": None,
                "summary": _summary(state=None, structure=None),
                "findings": [],
                "readiness": {
                    "ready": False,
                    "state": None,
                    "blocked_reason": "no_assessment",
                    "content_hash": "a" * 64,
                },
            }
        ),
    ), patch(
        "contextedge.services.playbook_quality_service.assess_playbook",
        AsyncMock(),
    ) as assess:
        await playbooks.get_playbook_quality(
            playbook_id=playbook_id,
            db=SimpleNamespace(),
            user=make_user(tenant_id=tenant_id),
        )

    assess.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_playbook_quality_404_for_foreign_tenant():
    tenant_id = uuid4()
    with patch.object(
        playbooks,
        "_load_tenant_playbook",
        AsyncMock(side_effect=HTTPException(status_code=404, detail="Playbook not found")),
    ):
        with pytest.raises(HTTPException) as exc:
            await playbooks.get_playbook_quality(
                playbook_id=uuid4(),
                db=SimpleNamespace(),
                user=make_user(tenant_id=tenant_id),
            )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_list_playbooks_attaches_quality_when_requested():
    tenant_id = uuid4()
    pb_id = uuid4()
    version_id = uuid4()
    now = datetime.now(UTC)
    playbook = SimpleNamespace(
        id=pb_id,
        tenant_id=tenant_id,
        domain_id=None,
        stable_key="pb-test",
        title="Restart agent",
        description=None,
        lifecycle_state="candidate",
        risk_tier="medium",
        automation_mode="suggest_only",
        approval_policy_id=None,
        owner_user_id=uuid4(),
        reviewer_user_id=None,
        approver_user_id=None,
        current_version_id=version_id,
        last_validated_at=None,
        expiry_at=None,
        pattern_id=None,
        created_at=now,
        updated_at=now,
    )
    version = SimpleNamespace(
        id=version_id,
        playbook_id=pb_id,
        semantic_version="0.1.0",
        trigger_conditions={},
        branching_logic={},
        inputs=[],
        outputs=[],
        steps=[{"step_id": "s1", "order": 1, "type": "remediation", "text": "Restart."}],
        rollback_notes=None,
        evidence_refs=None,
        conflicts=None,
        generation_provenance=None,
        playbook_confidence=0.5,
        execution_confidence_guidance=None,
        verification_policy=None,
    )
    assessment = SimpleNamespace(
        id=uuid4(),
        playbook_id=pb_id,
        content_hash=content_hash(build_content(playbook, version)),
        overall_state="inconclusive",
        dimension_states={"structure": "pass", "step_accuracy": "inconclusive"},
        completed_at=now,
        created_at=now,
        stale_reason=None,
        evaluation_mode="shadow",
    )

    db = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                _ScalarsResult([playbook]),
                _ScalarsResult([(pb_id, 0.8)]),
                _ScalarsResult([version]),
            ]
        )
    )

    with patch(
        "contextedge.services.playbook_quality_service.assessments_for_playbooks",
        AsyncMock(return_value={pb_id: assessment}),
    ), patch(
        "contextedge.services.playbook_quality_service.finding_counts_for",
        AsyncMock(return_value={assessment.id: {"critical": 0, "major": 0, "minor": 0, "info": 1}}),
    ):
        rows = await playbooks.list_playbooks(
            db=db,
            user=make_user(tenant_id=tenant_id),
            include_quality=True,
            limit=50,
            offset=0,
        )

    assert len(rows) == 1
    assert isinstance(rows[0].quality, PlaybookQualitySummary)
    assert rows[0].quality.state == "inconclusive"
    assert rows[0].quality.coverage.decided >= 1
    assert rows[0].quality.matches_current_content is True


@pytest.mark.asyncio
async def test_list_playbooks_omits_quality_by_default():
    tenant_id = uuid4()
    now = datetime.now(UTC)
    playbook = SimpleNamespace(
        id=uuid4(),
        tenant_id=tenant_id,
        domain_id=None,
        stable_key="pb-test",
        title="t",
        description=None,
        lifecycle_state="candidate",
        risk_tier="medium",
        automation_mode="suggest_only",
        approval_policy_id=None,
        owner_user_id=uuid4(),
        reviewer_user_id=None,
        approver_user_id=None,
        current_version_id=None,
        last_validated_at=None,
        expiry_at=None,
        pattern_id=None,
        created_at=now,
        updated_at=now,
    )
    db = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                _ScalarsResult([playbook]),
                _ScalarsResult([]),
            ]
        )
    )

    with patch(
        "contextedge.services.playbook_quality_service.assessments_for_playbooks",
        AsyncMock(),
    ) as batch:
        rows = await playbooks.list_playbooks(
            db=db,
            user=make_user(tenant_id=tenant_id),
            include_quality=False,
            limit=50,
            offset=0,
        )

    batch.assert_not_awaited()
    assert rows[0].quality is None
