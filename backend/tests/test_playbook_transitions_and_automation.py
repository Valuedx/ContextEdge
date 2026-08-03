"""Two governance controls that were unreachable or wrong from the UI.

The lifecycle map had been copied into the frontend under a comment
claiming it mirrored the backend, and it had drifted in both directions:
it offered transitions the API rejects, and — the costly half — it
omitted ``approved -> restricted``, so the one control for narrowing a
live playbook could not be reached from the UI at all.

And automation mode, the switch deciding whether a playbook may act on a
real system, was rendered in four places and editable in none. Every
generated playbook sat at ``suggest_only`` forever, which caps every
caller at read_only regardless of role — so the per-step approval
machinery below it could never actually engage.
"""

from __future__ import annotations

import datetime
import uuid

import pytest

from contextedge.schemas.playbook import PlaybookResponse, PlaybookUpdate
from contextedge.services.approval_policy_service import (
    ApprovalPolicy,
    ApprovalPolicyViolation,
    check_automation_mode,
)
from contextedge.services.playbook_service import VALID_TRANSITIONS


def _playbook(state: str, mode: str = "suggest_only") -> PlaybookResponse:
    now = datetime.datetime.now(datetime.UTC)
    return PlaybookResponse(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        domain_id=None,
        stable_key="pb-test",
        title="t",
        description=None,
        lifecycle_state=state,
        risk_tier="medium",
        automation_mode=mode,
        approval_policy_id=None,
        owner_user_id=uuid.uuid4(),
        reviewer_user_id=None,
        approver_user_id=None,
        current_version_id=None,
        last_validated_at=None,
        expiry_at=None,
        created_at=now,
        updated_at=now,
    )


# --- lifecycle transitions ----------------------------------------------------


@pytest.mark.parametrize("state", sorted(VALID_TRANSITIONS))
def test_the_api_serves_exactly_what_the_backend_enforces(state):
    """The parity guarantee that replaces the hand-maintained copy.

    Two copies of a rule is one copy too many when only one of them is
    enforced.
    """
    assert set(_playbook(state).allowed_transitions) == VALID_TRANSITIONS[state]


def test_restricting_a_live_playbook_is_reachable():
    """The transition the drifted UI map omitted. `restricted` is how a
    reviewer narrows an approved playbook when something looks wrong;
    without it the only route was the API directly."""
    assert "restricted" in _playbook("approved").allowed_transitions


def test_sending_an_approved_playbook_back_for_re_review_is_reachable():
    assert "under_review" in _playbook("approved").allowed_transitions


def test_transitions_the_api_rejects_are_never_offered():
    """The drifted map offered these two, so the buttons 400'd on click."""
    assert "retired" not in _playbook("candidate").allowed_transitions
    assert "retired" not in _playbook("under_review").allowed_transitions


def test_a_terminal_state_offers_nothing():
    assert _playbook("retired").allowed_transitions == []


def test_an_unrecognised_state_offers_nothing_rather_than_guessing():
    assert _playbook("something_new").allowed_transitions == []


# --- automation mode ----------------------------------------------------------


def test_every_automation_mode_is_accepted_by_the_update_schema():
    """The UI offers the full ladder; the schema must not reject a rung."""
    from contextedge.models.playbook import AUTOMATION_MODES

    for mode in AUTOMATION_MODES:
        assert PlaybookUpdate(automation_mode=mode).automation_mode == mode


def test_an_invalid_automation_mode_is_rejected():
    with pytest.raises(Exception):
        PlaybookUpdate(automation_mode="yolo")


def test_a_policy_ceiling_rejects_a_more_autonomous_mode():
    """Enforced at write time now, not only at execution.

    Saving a mode above the ceiling and failing later meant the error
    surfaced far from the screen where the choice was made, and read as a
    broken run rather than a policy decision.
    """
    policy = ApprovalPolicy(policy_id=uuid.uuid4(), max_automation_mode="supervised")
    with pytest.raises(ApprovalPolicyViolation):
        check_automation_mode(policy, "full_auto")


def test_a_policy_ceiling_permits_modes_at_or_below_it():
    policy = ApprovalPolicy(policy_id=uuid.uuid4(), max_automation_mode="supervised")
    for mode in ("suggest_only", "shadow", "human_confirmed", "supervised"):
        check_automation_mode(policy, mode)  # must not raise


def test_no_policy_means_no_ceiling():
    policy = ApprovalPolicy(policy_id=None)
    check_automation_mode(policy, "full_auto")  # must not raise


def test_binding_or_clearing_an_approval_policy_demands_tenant_admin():
    """Attaching a policy only adds constraints — but the same field
    DETACHES one, and clearing it drops the two-person rule, the
    approver-role requirement and the autonomy ceiling in a single write.
    A privilege is defined by the most dangerous thing it permits."""
    import inspect

    from contextedge.api.v1 import playbooks

    source = inspect.getsource(playbooks.update_playbook)
    policy_block = source.index('if "approval_policy_id" in update_data:')
    guard = source.index('require_role("tenant_admin")', policy_block)
    apply = source.index("setattr(playbook, field, value)")
    assert policy_block < guard < apply


def test_an_inactive_policy_cannot_be_bound():
    """It fails closed at execution, so binding it would only surface the
    problem later, as a broken run rather than a bad choice."""
    from contextedge.services.approval_policy_service import load_approval_policy

    import inspect

    source = inspect.getsource(load_approval_policy)
    assert "not row.is_active" in source
    # And the PATCH loads the policy, so that check runs at bind time.
    from contextedge.api.v1 import playbooks

    assert "load_approval_policy" in inspect.getsource(playbooks.update_playbook)


def test_changing_automation_mode_demands_tenant_admin():
    """Narrower than editing the playbook, on purpose.

    Authoring a procedure and authorising it to take destructive action
    are different privileges, and knowledge_manager holds the first.
    """
    import inspect

    from contextedge.api.v1 import playbooks

    source = inspect.getsource(playbooks.update_playbook)
    guard = source.index('require_role("tenant_admin")')
    apply = source.index("setattr(playbook, field, value)")
    # The role check must precede anything being written.
    assert guard < apply
