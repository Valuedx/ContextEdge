"""F7 — an approval is bound to the exact artifact it approved.

Nothing tied an approval to the thing that later ran: `ApprovalRequest`
recorded who approved and when, while the step payload lived in mutable JSONB
with no content hash. "Which exact artifact did the human approve?" was
unanswerable, so v6 invariant 2 could not be enforced.

The tests below are mostly about the ways a hash check gets this wrong: false
mismatches on re-serialization (which get the check disabled), an approval for
one playbook satisfying another that holds an identical step, and retro-blocking
approvals granted before the mechanism existed.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from contextedge.services.artifact_binding_service import (
    APPROVAL_VALIDITY_HOURS,
    ApprovalExpired,
    ArtifactBindingError,
    approval_expiry,
    canonical_hash,
    hash_step_artifact,
    verify_binding,
)

_PB = uuid.uuid4()
_VER = uuid.uuid4()


def _hash(step, *, step_index=0, version_id=_VER, playbook_id=_PB, semantic="1.0.0"):
    return hash_step_artifact(
        playbook_id=playbook_id,
        playbook_version_id=version_id,
        semantic_version=semantic,
        step_index=step_index,
        step=step,
    )


# =========================================================================
# Canonicalization — the reason this is RFC 8785 and not json.dumps
# =========================================================================


def test_key_order_and_whitespace_do_not_change_the_hash():
    """A naive hash produces false mismatches on re-serialization, and a check
    that cries wolf on every legitimate execution gets switched off."""
    a = {"title": "Renew the certificate", "safety_class": "high_side_effect", "n": 1}
    b = {"n": 1, "safety_class": "high_side_effect", "title": "Renew the certificate"}
    assert canonical_hash(a) == canonical_hash(b)


def test_nested_key_order_also_does_not_matter():
    a = {"inputs": {"host": "vpn-gw-east-01", "port": 443}, "id": 1}
    b = {"id": 1, "inputs": {"port": 443, "host": "vpn-gw-east-01"}}
    assert canonical_hash(a) == canonical_hash(b)


def test_a_real_change_changes_the_hash():
    base = {"title": "Renew the certificate", "target": "vpn-gw-east-01"}
    changed = {"title": "Renew the certificate", "target": "vpn-gw-west-02"}
    assert canonical_hash(base) != canonical_hash(changed)


def test_list_order_is_significant():
    """Steps are ordered. Sorting them would make a reordered procedure hash
    identically to the original, which is exactly the change worth catching."""
    assert canonical_hash([1, 2]) != canonical_hash([2, 1])


def test_an_uncanonicalizable_payload_is_refused_not_downgraded():
    """Silently falling back to a weaker encoding would produce a hash that
    means less than it appears to."""
    with pytest.raises(ArtifactBindingError, match="cannot be canonicalized"):
        canonical_hash({"threshold": float("nan")})


def test_the_hash_is_prefixed_so_the_algorithm_travels_with_it():
    digest = canonical_hash({"a": 1})
    assert digest.startswith("sha256:")
    assert len(digest) == len("sha256:") + 64


# =========================================================================
# The preimage — this step, of this version, of this playbook
# =========================================================================


def test_an_identical_step_in_another_playbook_hashes_differently():
    """Two playbooks can hold identical steps. Hashing the step alone would
    let an approval for one satisfy execution of the other — a confused-deputy
    problem wearing a content hash as a disguise."""
    step = {"title": "Restart the ordering service"}
    assert _hash(step) != _hash(step, playbook_id=uuid.uuid4())
    assert _hash(step) != _hash(step, version_id=uuid.uuid4())


def test_the_same_step_at_another_index_hashes_differently():
    step = {"title": "Restart the ordering service"}
    assert _hash(step, step_index=0) != _hash(step, step_index=1)


def test_a_republished_version_number_changes_the_hash():
    step = {"title": "Restart the ordering service"}
    assert _hash(step, semantic="1.0.0") != _hash(step, semantic="1.0.1")


# =========================================================================
# Verification
# =========================================================================


def test_an_unchanged_artifact_verifies():
    digest = _hash({"title": "Renew the certificate"})
    verify_binding(approved_hash=digest, current_hash=digest, expires_at=approval_expiry())


def test_a_one_character_change_blocks_execution():
    approved = _hash({"title": "Renew the certificate on vpn-gw-east-01"})
    current = _hash({"title": "Renew the certificate on vpn-gw-east-02"})
    with pytest.raises(ArtifactBindingError, match="not the artifact that was approved"):
        verify_binding(
            approved_hash=approved, current_hash=current, expires_at=approval_expiry()
        )


def test_an_expired_approval_blocks_execution_even_when_unchanged():
    digest = _hash({"title": "Renew the certificate"})
    past = datetime.now(UTC) - timedelta(minutes=1)
    with pytest.raises(ApprovalExpired, match="approval expired"):
        verify_binding(approved_hash=digest, current_hash=digest, expires_at=past)


def test_a_naive_datetime_expiry_is_treated_as_utc_not_crashed_on():
    digest = _hash({"title": "Renew the certificate"})
    naive_past = (datetime.now(UTC) - timedelta(hours=1)).replace(tzinfo=None)
    with pytest.raises(ApprovalExpired):
        verify_binding(approved_hash=digest, current_hash=digest, expires_at=naive_past)


def test_an_approval_predating_f7_is_allowed_through():
    """Retro-blocking every approval granted before the mechanism existed
    would break running deployments to enforce a rule they had no way to
    satisfy. It ages out on its own."""
    verify_binding(approved_hash=None, current_hash=_hash({"a": 1}), expires_at=None)


def test_expiry_is_the_incident_working_span_not_the_pending_window():
    """Distinct from the 72h that expires an UNANSWERED request: that is about
    nobody answering, this is about the answer going stale."""
    from contextedge.services.approval_expiry_service import APPROVAL_EXPIRY_HOURS

    assert APPROVAL_VALIDITY_HOURS < APPROVAL_EXPIRY_HOURS
    delta = approval_expiry(datetime(2026, 8, 16, 12, 0, tzinfo=UTC)) - datetime(
        2026, 8, 16, 12, 0, tzinfo=UTC
    )
    assert delta == timedelta(hours=APPROVAL_VALIDITY_HOURS)


# =========================================================================
# The enforcement point
# =========================================================================


@pytest.mark.asyncio
async def test_recording_a_tool_invocation_blocks_a_mutated_step():
    """The last moment before a tool actually runs."""
    from contextedge.models.execution import ApprovalRequest, ExecutionRun, ExecutionStepRun
    from contextedge.models.playbook import PlaybookVersion
    from contextedge.services.execution_service import (
        ExecutionPolicyError,
        assert_approved_artifact_unchanged,
    )

    tenant_id = uuid.uuid4()
    step = SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        step_index=0,
        execution_run_id=uuid.uuid4(),
        # What the step says NOW — the approver saw the east gateway.
        inputs={"title": "Renew the certificate on vpn-gw-west-02"},
    )
    approved = _hash({"title": "Renew the certificate on vpn-gw-east-01"})
    approval = SimpleNamespace(
        id=uuid.uuid4(), artifact_hash=approved, expires_at=approval_expiry()
    )
    run = SimpleNamespace(
        id=step.execution_run_id,
        tenant_id=tenant_id,
        playbook_id=_PB,
        playbook_version_id=_VER,
    )
    version = SimpleNamespace(id=_VER, semantic_version="1.0.0")

    class _Result:
        def scalars(self):
            return SimpleNamespace(all=lambda: [approval])

    async def _get(model, identity):
        return {ExecutionRun: run, PlaybookVersion: version}.get(model)

    db = SimpleNamespace(execute=AsyncMock(return_value=_Result()), get=AsyncMock(side_effect=_get))

    with (
        patch(
            "contextedge.services.execution_service.append_operational_event", AsyncMock()
        ) as event,
        pytest.raises(ExecutionPolicyError, match="not the artifact that was approved"),
    ):
        await assert_approved_artifact_unchanged(db, tenant_id, step)

    # The violation is recorded, not just raised: an attempt to execute
    # something other than what was approved is exactly the audit event.
    assert event.await_count == 1
    assert event.await_args.kwargs["event_type"] == "approval.binding_violated"
    _ = (ApprovalRequest, ExecutionStepRun)  # imported for the type names above


@pytest.mark.asyncio
async def test_a_step_with_no_approval_needs_no_verification():
    from contextedge.services.execution_service import assert_approved_artifact_unchanged

    class _Empty:
        def scalars(self):
            return SimpleNamespace(all=list)

    db = SimpleNamespace(execute=AsyncMock(return_value=_Empty()), get=AsyncMock())
    step = SimpleNamespace(id=uuid.uuid4(), tenant_id=uuid.uuid4(), step_index=0)
    await assert_approved_artifact_unchanged(db, step.tenant_id, step)
    db.get.assert_not_awaited()
