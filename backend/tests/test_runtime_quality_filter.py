"""The runtime filter must drop bad playbooks — and only bad playbooks.

The regression this pins: ``filter_runtime_eligible`` compared the assessment's
content hash against a hash it computed itself, and the caller in
``search/playbook_candidates.py`` did not pass the versions. The content hash
spans the shell *and* the version, so a hash built with the version missing can
never equal the stored one — every assessed playbook in the tenant was excluded,
and the log line ("dropped=N kept=0") read like the filter working.

``hybrid_ranker`` passed its versions and was unaffected, which is what made it
survive review: one of the two call sites was correct.

The fix loads missing versions inside the filter and treats an unknown live hash
as "cannot compare", not as a mismatch.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from contextedge.quality.hashing import content_hash
from contextedge.quality.revision import build_content
from contextedge.search.quality_filter import (
    assessment_excludes_runtime,
    filter_runtime_eligible,
)


def _assessment(state: str = "pass", *, hash_: str = "a" * 64, stale_at=None):
    return SimpleNamespace(overall_state=state, content_hash=hash_, stale_at=stale_at)


def _playbook(**kwargs):
    base = dict(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        title="Restart the agent",
        description=None,
        risk_tier="medium",
        automation_mode="suggest_only",
        domain_id=None,
        current_version_id=None,
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def _version(**kwargs):
    base = dict(
        id=uuid.uuid4(),
        semantic_version="1.0.0",
        trigger_conditions={},
        branching_logic={},
        inputs=[],
        outputs=[],
        steps=[{"step_id": "s1", "order": 1, "type": "remediation", "text": "Restart."}],
        rollback_notes=None,
        evidence_refs=None,
        conflicts=None,
        generation_provenance=None,
        playbook_confidence=0.8,
        execution_confidence_guidance=None,
        verification_policy=None,
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_a_missing_version_produces_a_different_hash():
    """The mechanism behind the bug, isolated.

    If this ever stops being true the filter's hash comparison has quietly
    changed meaning, and the guard below stops guarding anything.
    """
    playbook, version = _playbook(), _version()
    with_version = content_hash(build_content(playbook, version))
    without_version = content_hash(build_content(playbook, None))
    assert with_version != without_version


def test_unknown_live_hash_does_not_exclude():
    """The fix.

    An unknown is the one input that, treated as a mismatch, empties the whole
    corpus instead of dropping the bad rows.
    """
    assert assessment_excludes_runtime(_assessment(), live_content_hash=None) is False


def test_a_real_mismatch_still_excludes():
    assert assessment_excludes_runtime(_assessment(), live_content_hash="b" * 64) is True


def test_matching_hash_and_good_state_is_kept():
    assert assessment_excludes_runtime(_assessment(), live_content_hash="a" * 64) is False


def test_no_assessment_is_not_an_exclusion():
    # Legacy content nothing has looked at yet stays retrievable; withholding
    # it is a rollout policy decision, not this function's call to make.
    assert assessment_excludes_runtime(None, live_content_hash="a" * 64) is False


def test_failed_error_and_stale_states_are_excluded():
    for state in ("fail", "error", "stale"):
        assert (
            assessment_excludes_runtime(_assessment(state), live_content_hash="a" * 64)
            is True
        ), state


def test_a_stale_flag_excludes_even_when_the_state_still_reads_clean():
    assert (
        assessment_excludes_runtime(
            _assessment("pass", stale_at="2026-09-01T00:00:00Z"),
            live_content_hash="a" * 64,
        )
        is True
    )


def test_inconclusive_is_not_excluded():
    """Deliberate: most dimensions are inconclusive until Stage C ships.

    Excluding on it would take the entire corpus out of runtime for a reason
    that says nothing about the playbook.
    """
    assert (
        assessment_excludes_runtime(_assessment("inconclusive"), live_content_hash="a" * 64)
        is False
    )


@pytest.mark.asyncio
async def test_filter_survives_when_versions_not_supplied():
    """Integration-style: filter loads versions and keeps a matching assessment."""
    tenant_id = uuid.uuid4()
    playbook_id = uuid.uuid4()
    version_id = uuid.uuid4()
    playbook = _playbook(id=playbook_id, current_version_id=version_id)
    version = _version(id=version_id)
    live_hash = content_hash(build_content(playbook, version))
    assessment = _assessment(hash_=live_hash)

    class _Result:
        def scalars(self):
            return SimpleNamespace(all=lambda: [version])

    db = AsyncMock()
    db.execute = AsyncMock(return_value=_Result())

    with patch(
        "contextedge.search.quality_filter.assessments_for_playbooks",
        AsyncMock(return_value={playbook_id: assessment}),
    ):
        kept = await filter_runtime_eligible(
            db,
            tenant_id,
            {playbook_id: playbook},
        )

    assert playbook_id in kept
    db.execute.assert_awaited()
