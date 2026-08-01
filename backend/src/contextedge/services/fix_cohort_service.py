"""Cohort outcome recording + reviewer-gated promotion (backlog B5).

The learning-and-promotion policy from Doc-3, with one invariant the
goal makes explicit: **scope only ever broadens through a human**.

- One success = a precedent (the exact-CI/fix history that already
  exists), never a rule.
- Sustained same-model success mints a *candidate* rule for that model
  — ``approval_requirement='review'``, so it recommends nothing
  unreviewed above the review threshold.
- Class- and family-level candidates require success across DISTINCT
  narrower cohorts (two models make a class candidate; two classes
  make a family candidate).
- **Failures narrow**: any failure in a cohort blocks candidate
  creation for that cohort AND anything broader — "works on laptops,
  fails on desktops" stays laptop-only automatically; only a reviewer
  can override by editing rules.
"""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.models.entity import Entity
from contextedge.models.error_signature import FixPattern
from contextedge.models.fix_applicability import FixApplicabilityRule
from contextedge.models.fix_cohort import FixCohortStat
from contextedge.services.entity_class_service import canonical_class_for
from contextedge.services.fix_applicability_service import _class_chain

logger = structlog.get_logger()

PROMOTE_MODEL_MIN_SUCCESSES = 2
PROMOTE_CLASS_MIN_DISTINCT_MODELS = 2
PROMOTE_FAMILY_MIN_DISTINCT_CLASSES = 2
PROMOTION_CREATED_BY = "promotion_policy"


async def cohorts_for_entity(
    db: AsyncSession, entity: Entity
) -> list[tuple[str, str]]:
    """(cohort_type, cohort_key) at each grain the entity supports.
    Absent traits produce no cohort — a CI without a model simply has
    no model cohort, never a guessed one."""
    cohorts: list[tuple[str, str]] = []
    if entity.model:
        cohorts.append(("model", entity.model))
    class_key = canonical_class_for((entity.attributes or {}).get("ci_class"))
    cohorts.append(("class", class_key))
    chain = await _class_chain(db, class_key)
    if len(chain) > 1:
        cohorts.append(("family", chain[1]))
    return cohorts


async def record_fix_outcome(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    fix_pattern_id: uuid.UUID,
    entity: Entity,
    success: bool,
) -> dict:
    """Count one outcome at every cohort grain + the fix's own global
    counters, then re-evaluate promotions."""
    counts = {"cohorts": 0, "candidate_rules": 0}
    fix = await db.get(FixPattern, fix_pattern_id)
    if fix is None or fix.tenant_id != tenant_id:
        counts["error"] = "fix_not_found"
        return counts

    if success:
        fix.success_count = (fix.success_count or 0) + 1
    else:
        fix.failure_count = (fix.failure_count or 0) + 1

    for cohort_type, cohort_key in await cohorts_for_entity(db, entity):
        stat = (
            await db.execute(
                select(FixCohortStat).where(
                    FixCohortStat.fix_pattern_id == fix_pattern_id,
                    FixCohortStat.cohort_type == cohort_type,
                    FixCohortStat.cohort_key == cohort_key,
                )
            )
        ).scalar_one_or_none()
        if stat is None:
            stat = FixCohortStat(
                tenant_id=tenant_id,
                fix_pattern_id=fix_pattern_id,
                cohort_type=cohort_type,
                cohort_key=cohort_key,
                success_count=0,
                failure_count=0,
            )
            try:
                async with db.begin_nested():
                    db.add(stat)
                    await db.flush()
            except IntegrityError:
                stat = (
                    await db.execute(
                        select(FixCohortStat).where(
                            FixCohortStat.fix_pattern_id == fix_pattern_id,
                            FixCohortStat.cohort_type == cohort_type,
                            FixCohortStat.cohort_key == cohort_key,
                        )
                    )
                ).scalar_one()
        if success:
            stat.success_count = (stat.success_count or 0) + 1
        else:
            stat.failure_count = (stat.failure_count or 0) + 1
        counts["cohorts"] += 1

    counts["candidate_rules"] = await evaluate_promotions(
        db, tenant_id, fix_pattern_id
    )
    return counts


async def _has_promotion_rule(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    fix_pattern_id: uuid.UUID,
    *,
    target_class_key: str | None,
    required_traits: dict,
) -> bool:
    rows = (
        (
            await db.execute(
                select(FixApplicabilityRule).where(
                    FixApplicabilityRule.tenant_id == tenant_id,
                    FixApplicabilityRule.fix_pattern_id == fix_pattern_id,
                    FixApplicabilityRule.created_by == PROMOTION_CREATED_BY,
                )
            )
        )
        .scalars()
        .all()
    )
    return any(
        r.target_class_key == target_class_key
        and (r.required_traits or {}) == required_traits
        for r in rows
    )


async def evaluate_promotions(
    db: AsyncSession, tenant_id: uuid.UUID, fix_pattern_id: uuid.UUID
) -> int:
    """Mint review-gated candidate rules the current stats justify.
    Idempotent; failures block their cohort and everything broader."""
    stats = (
        (
            await db.execute(
                select(FixCohortStat).where(
                    FixCohortStat.tenant_id == tenant_id,
                    FixCohortStat.fix_pattern_id == fix_pattern_id,
                )
            )
        )
        .scalars()
        .all()
    )
    by_type: dict[str, list[FixCohortStat]] = {"model": [], "class": [], "family": []}
    for s in stats:
        by_type.setdefault(s.cohort_type, []).append(s)

    created = 0

    async def _mint(target_class_key, required_traits, level, cohort_desc):
        nonlocal created
        if await _has_promotion_rule(
            db,
            tenant_id,
            fix_pattern_id,
            target_class_key=target_class_key,
            required_traits=required_traits,
        ):
            return
        db.add(
            FixApplicabilityRule(
                tenant_id=tenant_id,
                fix_pattern_id=fix_pattern_id,
                target_class_key=target_class_key,
                required_traits=required_traits,
                excluded_traits={},
                applicability_level=level,
                minimum_evidence=PROMOTE_MODEL_MIN_SUCCESSES,
                confidence=0.5,
                approval_requirement="review",  # scope broadens via humans only
                created_by=PROMOTION_CREATED_BY,
            )
        )
        await db.flush()
        created += 1
        logger.info(
            "fix_promotion.candidate_minted",
            tenant_id=str(tenant_id),
            fix_pattern_id=str(fix_pattern_id),
            cohort=cohort_desc,
            level=level,
        )

    successful_models = [
        s
        for s in by_type["model"]
        if s.failure_count == 0
        and s.success_count >= PROMOTE_MODEL_MIN_SUCCESSES
    ]
    for s in successful_models:
        await _mint(
            None,
            {"model": s.cohort_key},
            "same_model_and_configuration",
            f"model:{s.cohort_key}",
        )

    for s in by_type["class"]:
        if s.failure_count:
            continue  # failures narrow: no class candidate, ever, automatically
        # The class itself must have direct successes too — two proven
        # models from some OTHER class never justify a rule here.
        if s.success_count < PROMOTE_MODEL_MIN_SUCCESSES:
            continue
        if len(successful_models) >= PROMOTE_CLASS_MIN_DISTINCT_MODELS:
            await _mint(
                s.cohort_key,
                {},
                "same_ci_class",
                f"class:{s.cohort_key}",
            )

    successful_classes = [
        s for s in by_type["class"] if s.failure_count == 0 and s.success_count > 0
    ]
    for s in by_type["family"]:
        if s.failure_count:
            continue
        if s.success_count < PROMOTE_MODEL_MIN_SUCCESSES:
            continue
        if len(successful_classes) >= PROMOTE_FAMILY_MIN_DISTINCT_CLASSES:
            await _mint(
                s.cohort_key,
                {},
                "related_ci_class",
                f"family:{s.cohort_key}",
            )
    return created
