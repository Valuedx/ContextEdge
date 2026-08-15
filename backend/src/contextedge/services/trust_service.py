"""Scoring and applying scoped trust (F10).

Autonomy today is a mode on the playbook — a configuration, not a track
record. This module keeps the record and turns it into a verdict.

Three properties are load-bearing:

1. **The lower bound, not the rate.** 3/3 is a rate of 1.0 and means almost
   nothing; 340/350 is 0.97 and means a great deal. A Wilson score interval
   scores the small sample as uncertain instead of relying on a separate
   minimum-sample rule that eventually gets tuned away.
2. **Recent failure beats the long-run average.** A profile with 400 verified
   successes and three failures in a row is not trustworthy right now. The
   streak suspends it without waiting for the average to move and without a
   deploy.
3. **Trust vetoes; it never grants.** ``autonomous`` means *trust is not the
   reason to stop* — policy still has to permit the action. This is v6 §25's
   own rule, and inverting it is how a measured track record turns into an
   automatic escalation of privilege.

Outcomes come from F9's assessment, which is why F9 shipped first: fed by the
old silence-equals-success verifier, every number here would have been
inflated in exactly the direction that matters.
"""

from __future__ import annotations

import math
import uuid
from datetime import UTC, datetime

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.models.trust import UNSCOPED, TrustProfile

logger = structlog.get_logger()

# z for a 95% one-sided interval. Named rather than inlined because the
# confidence level is a policy choice someone will want to see and argue with.
WILSON_Z = 1.96

# The lower bound an unattended action has to clear. Deliberately high: this
# is the threshold for doing something to production with nobody watching.
AUTONOMOUS_MIN_LOWER_BOUND = 0.90
# Below this, the record actively argues against even supervised execution.
SUPERVISED_MIN_LOWER_BOUND = 0.50
# Consecutive failures that suspend a scope regardless of its history.
SUSPEND_AFTER_CONSECUTIVE_FAILURES = 3


def wilson_lower_bound(successes: int, total: int, z: float = WILSON_Z) -> float:
    """Lower bound of the Wilson score interval for a success proportion.

    Zero observations is 0.0 — not 0.5, not "unknown". An unexercised scope has
    earned nothing, and starting it anywhere above the floor would let a scope
    become autonomous by never being tried.
    """
    if total <= 0:
        return 0.0
    successes = max(0, min(successes, total))
    phat = successes / total
    denominator = 1 + z**2 / total
    centre = phat + z**2 / (2 * total)
    margin = z * math.sqrt((phat * (1 - phat) + z**2 / (4 * total)) / total)
    return max(0.0, (centre - margin) / denominator)


def evaluate_autonomy(profile: TrustProfile) -> tuple[str, str]:
    """``(autonomy_level, reason)`` for a profile. Pure; no I/O.

    Order matters: the suspension check runs first, because a profile with a
    long good history and a bad last week must not be rescued by its own
    average.
    """
    if profile.consecutive_failures >= SUSPEND_AFTER_CONSECUTIVE_FAILURES:
        return (
            "suspended",
            f"{profile.consecutive_failures} consecutive non-successes — recent "
            "evidence overrides the long-run record",
        )
    if profile.sample_size == 0:
        return "advisory", "no outcomes recorded for this scope yet"

    bound = profile.confidence_lower_bound
    if bound >= AUTONOMOUS_MIN_LOWER_BOUND:
        return (
            "autonomous",
            f"{profile.verified_successes}/{profile.sample_size} verified, "
            f"lower bound {bound:.2f} ≥ {AUTONOMOUS_MIN_LOWER_BOUND} "
            "(policy must still permit the action)",
        )
    if bound >= SUPERVISED_MIN_LOWER_BOUND:
        return (
            "supervised",
            f"lower bound {bound:.2f} — enough evidence to act with a human "
            f"watching, not enough for {AUTONOMOUS_MIN_LOWER_BOUND}",
        )
    return (
        "advisory",
        f"lower bound {bound:.2f} on {profile.sample_size} outcome(s) — too "
        "little evidence, or too much of it negative, to execute",
    )


def scope_key(
    *,
    agent_ref: str | None,
    action_type: str | None,
    resource_class: str | None,
    environment: str | None,
    business_criticality: str | None,
) -> dict[str, str]:
    """Normalise a scope. Unknown dimensions become ``UNSCOPED``, never NULL.

    The scope is a unique key; NULLs would let two "unknown environment"
    profiles coexist for the same agent and action and quietly split the
    record in half.
    """
    return {
        "agent_ref": (agent_ref or UNSCOPED)[:120],
        "action_type": (action_type or UNSCOPED)[:60],
        "resource_class": (resource_class or UNSCOPED)[:80],
        "environment": (environment or UNSCOPED)[:30],
        "business_criticality": (business_criticality or UNSCOPED)[:30],
    }


async def get_profile(
    db: AsyncSession, tenant_id: uuid.UUID, scope: dict[str, str]
) -> TrustProfile | None:
    return (
        await db.execute(
            select(TrustProfile).where(
                TrustProfile.tenant_id == tenant_id,
                TrustProfile.agent_ref == scope["agent_ref"],
                TrustProfile.action_type == scope["action_type"],
                TrustProfile.resource_class == scope["resource_class"],
                TrustProfile.environment == scope["environment"],
                TrustProfile.business_criticality == scope["business_criticality"],
            )
        )
    ).scalar_one_or_none()


# Assessment results that count as a verified success. Only one, deliberately:
# ``partial_success`` and ``monitor_required`` are NOT successes, for the same
# reason F9 refuses to map them onto ``verified``.
SUCCESS_RESULTS = ("success",)
FAILURE_RESULTS = ("failed", "rollback_required", "partial_success")


async def record_outcome(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    scope: dict[str, str],
    assessment_result: str,
    rolled_back: bool = False,
    human_overrode: bool = False,
    now: datetime | None = None,
) -> TrustProfile:
    """Fold one verification outcome into its scope's record.

    An ``inconclusive`` outcome is counted in ``sample_size`` but is neither a
    success nor a failure: it drags the lower bound down (we tried and learned
    nothing) without pretending the action broke something. That is the honest
    treatment, and it is why F9 had to ship first — the old verifier would have
    filed most of these as successes.
    """
    now = now or datetime.now(UTC)
    profile = await get_profile(db, tenant_id, scope)
    if profile is None:
        profile = TrustProfile(tenant_id=tenant_id, **scope)
        db.add(profile)

    profile.sample_size += 1
    if assessment_result in SUCCESS_RESULTS:
        profile.verified_successes += 1
        profile.consecutive_failures = 0
    elif assessment_result in FAILURE_RESULTS:
        profile.failures += 1
        profile.consecutive_failures += 1
    else:
        profile.inconclusive += 1
        # Not a failure, but not a reason to reset the streak either: a run of
        # outcomes nobody could verify is its own kind of bad news.
        profile.consecutive_failures += 1

    if rolled_back:
        profile.rollbacks += 1
    if human_overrode:
        profile.human_overrides += 1

    profile.confidence_lower_bound = wilson_lower_bound(
        profile.verified_successes, profile.sample_size
    )
    profile.autonomy_level, profile.autonomy_reason = evaluate_autonomy(profile)
    profile.autonomy_reason = (profile.autonomy_reason or "")[:300]
    profile.last_outcome_at = now
    profile.last_evaluated_at = now
    await db.flush()

    logger.info(
        "trust.outcome_recorded",
        tenant_id=str(tenant_id),
        agent_ref=scope["agent_ref"],
        action_type=scope["action_type"],
        result=assessment_result,
        sample_size=profile.sample_size,
        lower_bound=round(profile.confidence_lower_bound, 3),
        autonomy_level=profile.autonomy_level,
    )
    return profile
