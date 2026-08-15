"""Binding an approval to the exact artifact it approved (F7).

Nothing tied an approval to the thing that later ran. ``ApprovalRequest``
recorded who approved and when; the step payload lived in
``PlaybookVersion.steps`` as mutable JSONB with no content hash. "Which exact
artifact did the human approve?" was not an answerable question, so v6
invariant 2 — *no execution of an artifact different from the approved artifact
hash* — was not enforceable.

**Why RFC 8785 and not ``json.dumps(sort_keys=True)``.** Key order, whitespace
and number formatting all change the bytes without changing the meaning, so a
naive hash produces false mismatches on re-serialization — and a system that
cries wolf on every legitimate execution gets its check disabled. JCS is the
IETF standard for exactly this: keys sorted by UTF-16 code unit, no
whitespace, ECMAScript number serialization. Strict conformance also buys
interop we do not need today but would have to retrofit later: an external
attestor can recompute our hash without our code.

**What is hashed** is the step *in its version*, not the step alone. Two
playbooks can hold identical steps, and an approval for one must not satisfy
execution of the other, so the identity of the version and the index of the
step are part of the preimage.

The hash is a self-consistency check, not a signature. It proves the payload
did not change between approval and execution; it does not prove who produced
it. Signing is a separate concern and is not claimed here.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import rfc8785
import structlog

logger = structlog.get_logger()

# How long a granted approval stays good for. Distinct from the 72h that
# expires an UNDECIDED request (approval_expiry_service): that one is about
# nobody answering, this one is about the answer going stale. Four hours is
# the incident-response working span — long enough that an approver is not
# re-asked mid-incident, short enough that yesterday's approval cannot
# authorise today's run.
APPROVAL_VALIDITY_HOURS = 4

HASH_PREFIX = "sha256:"


class ArtifactBindingError(Exception):
    """The artifact about to execute is not the artifact that was approved."""


class ApprovalExpired(ArtifactBindingError):
    """The approval was valid, and no longer is."""


def canonical_hash(payload: Any) -> str:
    """``sha256:<hex>`` over the RFC 8785 canonicalization of *payload*.

    Raises ``ArtifactBindingError`` on a payload JCS cannot canonicalize —
    NaN, infinity, non-string keys. Refusing is right: a payload that cannot be
    canonicalized cannot be bound, and silently falling back to a weaker
    encoding would produce a hash that means less than it appears to.
    """
    try:
        canonical = rfc8785.dumps(payload)
    except (rfc8785.CanonicalizationError, ValueError, TypeError) as exc:
        raise ArtifactBindingError(
            f"artifact cannot be canonicalized for hashing: {exc}"
        ) from exc
    return HASH_PREFIX + hashlib.sha256(canonical).hexdigest()


def step_artifact_payload(
    *,
    playbook_id: uuid.UUID,
    playbook_version_id: uuid.UUID,
    semantic_version: str,
    step_index: int,
    step: Any,
) -> dict:
    """The preimage: this step, of this version, of this playbook.

    Two playbooks can hold identical steps. Hashing the step alone would let
    an approval for one satisfy execution of the other, which is a
    confused-deputy problem wearing a content hash as a disguise.
    """
    return {
        "playbook_id": str(playbook_id),
        "playbook_version_id": str(playbook_version_id),
        "semantic_version": semantic_version,
        "step_index": step_index,
        "step": step,
    }


def hash_step_artifact(
    *,
    playbook_id: uuid.UUID,
    playbook_version_id: uuid.UUID,
    semantic_version: str,
    step_index: int,
    step: Any,
) -> str:
    return canonical_hash(
        step_artifact_payload(
            playbook_id=playbook_id,
            playbook_version_id=playbook_version_id,
            semantic_version=semantic_version,
            step_index=step_index,
            step=step,
        )
    )


def approval_expiry(now: datetime | None = None) -> datetime:
    return (now or datetime.now(UTC)) + timedelta(hours=APPROVAL_VALIDITY_HOURS)


def verify_binding(
    *,
    approved_hash: str | None,
    current_hash: str,
    expires_at: datetime | None,
    now: datetime | None = None,
) -> None:
    """Raise unless the approved artifact is the one about to run.

    An approval carrying no hash predates F7. It is allowed through rather
    than refused: retro-blocking every approval granted before the mechanism
    existed would break running deployments to enforce a rule they had no way
    to satisfy. The absence is logged so it is visible rather than silent, and
    it disappears on its own as old approvals age out.
    """
    if not approved_hash:
        logger.info(
            "approval_binding.unbound_approval_allowed",
            reason="approval predates artifact binding (F7)",
        )
        return

    now = now or datetime.now(UTC)
    if expires_at is not None:
        deadline = expires_at if expires_at.tzinfo else expires_at.replace(tzinfo=UTC)
        if now > deadline:
            raise ApprovalExpired(
                f"approval expired at {deadline.isoformat()}; re-request it against "
                "current context rather than executing on a stale decision"
            )

    if approved_hash != current_hash:
        raise ArtifactBindingError(
            "the artifact about to execute is not the artifact that was approved "
            f"(approved {approved_hash[:23]}…, current {current_hash[:23]}…). "
            "The step payload changed after approval; re-approve it."
        )
