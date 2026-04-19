"""Tests for M2: PlaybookStep + VerificationPolicy schemas and the plumbing
through PlaybookVersionCreate."""

import pytest

from contextedge.schemas.playbook import (
    PlaybookStep,
    PlaybookVersionCreate,
    VerificationPolicy,
)


# =========================================================================
# PlaybookStep — defaults + back-compat
# =========================================================================


def test_empty_dict_validates_with_all_defaults():
    """Existing pre-M2 JSONB entries can be empty; schema must still accept."""
    s = PlaybookStep.model_validate({})
    assert s.reversible is False
    assert s.verification is False
    assert s.requires_approval is False
    assert s.time_estimate_sec is None
    assert s.title is None
    assert s.safety_class is None


def test_extra_fields_preserved():
    """extra='allow' lets vendor-specific metadata round-trip."""
    s = PlaybookStep.model_validate({
        "title": "Renew cert",
        "custom_field": "vendor-x",
        "nested": {"key": "val"},
    })
    dumped = s.model_dump()
    assert dumped["custom_field"] == "vendor-x"
    assert dumped["nested"] == {"key": "val"}


def test_full_zone_6_fields():
    """The fields Zone 6 renders round-trip with correct types."""
    s = PlaybookStep(
        index=3,
        title="Renew certificate via internal CA",
        description="Rotate the expiring gateway cert",
        safety_class="high_side_effect",
        requires_approval=True,
        reversible=False,
        time_estimate_sec=300,
        verification=False,
        rollback_hint="Rollback via CA revoke + re-issue prior cert",
        tool_ref="internal_ca.rotate",
    )
    assert s.safety_class == "high_side_effect"
    assert s.time_estimate_sec == 300
    assert s.requires_approval is True


def test_rejects_unknown_safety_class():
    with pytest.raises(ValueError, match="safety_class"):
        PlaybookStep(title="x", safety_class="super_unsafe")


def test_rejects_negative_time_estimate():
    with pytest.raises(ValueError):
        PlaybookStep(title="x", time_estimate_sec=-5)


def test_accepts_known_safety_classes():
    for safety in ("read_only", "low_side_effect", "high_side_effect", "destructive"):
        assert PlaybookStep(title="x", safety_class=safety).safety_class == safety


def test_verification_step_flag():
    """A verification step is distinct from an action step — the UI uses this
    flag to render the post-action recheck card."""
    s = PlaybookStep(
        title="Verify VPN auth succeeds",
        verification=True,
        reversible=True,
        time_estimate_sec=60,
    )
    assert s.verification is True


# =========================================================================
# VerificationPolicy
# =========================================================================


def test_verification_policy_defaults():
    p = VerificationPolicy()
    assert p.auto_close_on_success is False
    assert p.recheck_after_sec is None


def test_verification_policy_full():
    p = VerificationPolicy(
        auto_close_on_success=True,
        recheck_after_sec=1800,
        recheck_metric="cert_valid_until",
        recheck_source="intune",
    )
    assert p.auto_close_on_success is True
    assert p.recheck_after_sec == 1800
    assert p.recheck_source == "intune"


def test_verification_policy_rejects_negative_recheck():
    with pytest.raises(ValueError):
        VerificationPolicy(recheck_after_sec=-10)


# =========================================================================
# PlaybookVersionCreate — steps validated, verification_policy round-trips
# =========================================================================


def test_playbook_version_create_validates_step_list():
    """Each step dict is coerced through PlaybookStep; extras pass through."""
    body = PlaybookVersionCreate(
        semantic_version="1.0.0",
        steps=[
            {"title": "Step 1", "reversible": True, "time_estimate_sec": 15},
            {"title": "Step 2", "verification": True, "tool_ref": "intune.get_device"},
        ],
    )
    assert len(body.steps) == 2
    assert body.steps[0].reversible is True
    assert body.steps[1].verification is True
    assert body.steps[1].tool_ref == "intune.get_device"


def test_playbook_version_create_rejects_invalid_step():
    with pytest.raises(ValueError, match="safety_class"):
        PlaybookVersionCreate(
            steps=[{"title": "x", "safety_class": "fake"}],
        )


def test_playbook_version_create_verification_policy_round_trip():
    body = PlaybookVersionCreate(
        steps=[],
        verification_policy={
            "auto_close_on_success": True,
            "recheck_after_sec": 900,
            "recheck_metric": "disk_free_pct",
        },
    )
    assert body.verification_policy is not None
    assert body.verification_policy.auto_close_on_success is True
    # model_dump for the service layer still produces plain dicts.
    dumped = body.model_dump()
    assert dumped["verification_policy"]["recheck_after_sec"] == 900


def test_playbook_version_create_empty_is_valid():
    """Back-compat: callers writing minimal payloads still work."""
    body = PlaybookVersionCreate()
    assert body.steps == []
    assert body.verification_policy is None


# =========================================================================
# Model column presence
# =========================================================================


def test_playbook_version_model_has_verification_policy_column():
    from contextedge.models.playbook import PlaybookVersion
    cols = {c.name for c in PlaybookVersion.__table__.columns}
    assert "verification_policy" in cols
