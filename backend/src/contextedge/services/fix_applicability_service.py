"""Deterministic fix-applicability assessment (backlog B4).

The three-relationship separation (Doc-3): same occurrence is
correlation, similar problem is a signature/pattern, and *fix
applicability* is a precondition match — "is the fix that worked on
LPT001 safe and relevant for LPT121 or a desktop?" answered by
comparing the actual deciding traits, never CI class alone.

Decision policy:
- **Excluded trait present → rejected.** Hard veto.
- **Required trait unmet → rejected.** A desktop with a Realtek
  adapter never sees the AX201 driver-rollback fix; a rule requiring a
  trait the target simply does not carry is *not validated* (absent is
  absent, never assumed to match).
- Survivors get the explicit applicability level (7-level ladder), an
  additive transparent score (the factors list IS the explanation —
  change-risk pattern), the matching factors, the differences, and
  ``requires_review``. Weights are named constants, explicitly
  provisional until B5's cohort statistics calibrate them.
"""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.models.entity import Entity
from contextedge.models.entity_class import EntityClass
from contextedge.models.error_signature import FixPattern
from contextedge.models.fix_applicability import FixApplicabilityRule
from contextedge.services.entity_class_service import canonical_class_for

logger = structlog.get_logger()

# Scoring constants (Doc-3's factor table). Provisional until cohort
# statistics (B5) calibrate them from reviewed outcomes.
SCORE_ERROR_SIGNATURE = 0.25
SCORE_COMPONENT = 0.20
SCORE_VERSION = 0.15
SCORE_OS_BUILD = 0.10
SCORE_MODEL = 0.10
SCORE_RECENT_CHANGE = 0.10
SCORE_SAME_CLASS = 0.10
SCORE_RELATED_CLASS = 0.05
SCORE_OTHER_TRAIT = 0.05
PENALTY_DIFFERENT_MANUFACTURER = 0.05

# Below this score — or outside the exempt levels — a human reviews
# before the fix is recommended for execution.
REVIEW_EXEMPT_MIN_SCORE = 0.9

# Level floors: an exact-CI precedent and a full model+configuration
# match are "very high" by construction (Doc-3 examples 1 and the
# BitLocker case) — the additive factors then only raise them.
LEVEL_FLOORS = {
    "exact_ci": 0.95,
    "same_model_and_configuration": 0.9,
}

# Trait key → scoring bucket. Anything else scores SCORE_OTHER_TRAIT.
_TRAIT_SCORES = {
    "error_signature": SCORE_ERROR_SIGNATURE,
    "failure_mode": SCORE_ERROR_SIGNATURE,
    "component": SCORE_COMPONENT,
    "wifi_adapter": SCORE_COMPONENT,
    "failing_component": SCORE_COMPONENT,
    "driver_version": SCORE_VERSION,
    "software_version": SCORE_VERSION,
    "bios_package": SCORE_VERSION,
    "policy_version": SCORE_VERSION,
    "os_version": SCORE_OS_BUILD,
    "os_build": SCORE_OS_BUILD,
    "model": SCORE_MODEL,
    "recent_change": SCORE_RECENT_CHANGE,
}

# Entity columns consulted before the attributes JSON for a trait value.
_COLUMN_TRAITS = ("manufacturer", "model", "os_name", "os_version")


def _target_trait(entity: Entity, key: str):
    if key in _COLUMN_TRAITS:
        value = getattr(entity, key, None)
        if value is not None:
            return value
    return (entity.attributes or {}).get(key)


def _norm(value) -> str:
    return " ".join(str(value).split()).lower() if value is not None else ""


async def _class_chain(db: AsyncSession, class_key: str) -> list[str]:
    """canonical_key ancestor chain, target class first. Missing
    taxonomy (pre-0042) degrades to just the key itself."""
    chain = [class_key]
    current = (
        await db.execute(
            select(EntityClass).where(EntityClass.canonical_key == class_key)
        )
    ).scalar_one_or_none()
    for _ in range(8):
        if current is None or current.parent_class_id is None:
            break
        current = await db.get(EntityClass, current.parent_class_id)
        if current is not None:
            chain.append(current.canonical_key)
    return chain


def _level_for(
    *,
    exact_ci: bool,
    matched_keys: set[str],
    target_class_key: str,
    rule_class_key: str | None,
    class_chain: list[str],
) -> str:
    model_matched = "model" in matched_keys
    config_matched = bool({"os_version", "os_build", "bios_package"} & matched_keys)
    component_matched = bool(
        {
            "component",
            "wifi_adapter",
            "failing_component",
            "driver_version",
            "software_version",
            "policy_version",
        }
        & matched_keys
    )
    same_class = rule_class_key is not None and target_class_key == rule_class_key
    related_class = rule_class_key is not None and rule_class_key in class_chain

    if exact_ci:
        return "exact_ci"
    if model_matched and config_matched:
        return "same_model_and_configuration"
    if component_matched and (same_class or related_class or rule_class_key is None):
        return "same_component_or_version"
    if component_matched:
        return "cross_class_capability"
    if same_class:
        return "same_ci_class"
    if related_class:
        return "related_ci_class"
    return "semantic_only"


async def assess_fix_applicability(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    target_entity: Entity,
    *,
    limit: int = 10,
) -> dict:
    """Assess every rule-bearing fix pattern against one target CI.
    Returns applicable candidates (best score first) and the rejected
    ones with their reason — "no applicable precedent" is an honest
    empty list, not a stretched match."""
    target_class = canonical_class_for(
        (target_entity.attributes or {}).get("ci_class")
    )
    chain = await _class_chain(db, target_class)

    rows = (
        await db.execute(
            select(FixApplicabilityRule, FixPattern)
            .join(FixPattern, FixPattern.id == FixApplicabilityRule.fix_pattern_id)
            .where(FixApplicabilityRule.tenant_id == tenant_id)
            .limit(200)
        )
    ).all()

    applicable: list[dict] = []
    rejected: list[dict] = []
    for rule, fix in rows:
        exact_ci = (
            fix.workflow_entity_id is not None
            and fix.workflow_entity_id == target_entity.id
        )

        excluded_hits = [
            key
            for key, value in (rule.excluded_traits or {}).items()
            if _norm(_target_trait(target_entity, key)) == _norm(value)
        ]
        if excluded_hits:
            rejected.append(
                {
                    "fix_pattern_id": str(rule.fix_pattern_id),
                    "reason": "excluded_trait",
                    "traits": excluded_hits,
                }
            )
            continue

        matched: set[str] = set()
        differences: list[str] = []
        unmet: list[str] = []
        for key, value in (rule.required_traits or {}).items():
            target_value = _target_trait(target_entity, key)
            if target_value is None:
                unmet.append(key)
            elif _norm(target_value) == _norm(value):
                matched.add(key)
            else:
                differences.append(
                    f"{key}: target={target_value!r} required={value!r}"
                )
        if unmet or differences:
            rejected.append(
                {
                    "fix_pattern_id": str(rule.fix_pattern_id),
                    "reason": "required_trait_not_validated",
                    "unmet": unmet,
                    "differences": differences,
                }
            )
            continue

        level = _level_for(
            exact_ci=exact_ci,
            matched_keys=matched,
            target_class_key=target_class,
            rule_class_key=rule.target_class_key,
            class_chain=chain,
        )

        score = 0.0
        factors: list[str] = []
        for key in sorted(matched):
            weight = _TRAIT_SCORES.get(key, SCORE_OTHER_TRAIT)
            score += weight
            factors.append(f"{key} matched (+{weight})")
        if rule.target_class_key == target_class:
            score += SCORE_SAME_CLASS
            factors.append(f"same class {target_class} (+{SCORE_SAME_CLASS})")
        elif rule.target_class_key in chain[1:]:
            score += SCORE_RELATED_CLASS
            factors.append(
                f"target class {target_class} within {rule.target_class_key}"
                f" (+{SCORE_RELATED_CLASS})"
            )
        notable_differences: list[str] = []
        if level == "cross_class_capability":
            # Transferring across the class family (laptop fix on a
            # desktop): dampen and surface the difference (Doc-3 ex. 4).
            # A matched model implies the same OEM, so this penalty
            # never touches same-model matches.
            score -= PENALTY_DIFFERENT_MANUFACTURER
            notable_differences.append(
                f"target class {target_class} outside rule scope"
                f" {rule.target_class_key}"
            )
        if exact_ci:
            factors.append("exact CI precedent")
        score = max(score, LEVEL_FLOORS.get(level, 0.0))
        score = min(max(score, 0.0), 1.0)

        requires_review = (
            rule.approval_requirement == "review"
            and not (
                level in ("exact_ci", "same_model_and_configuration")
                and score >= REVIEW_EXEMPT_MIN_SCORE
            )
        ) or score < REVIEW_EXEMPT_MIN_SCORE

        applicable.append(
            {
                "fix_pattern_id": str(rule.fix_pattern_id),
                "recommended_fix": fix.recommended_fix,
                "applicability_level": level,
                "confidence": round(score, 2),
                "matching_factors": factors,
                "differences": notable_differences,
                "requires_review": requires_review,
            }
        )

    applicable.sort(key=lambda a: a["confidence"], reverse=True)
    return {
        "target_entity_id": str(target_entity.id),
        "target_class": target_class,
        "class_chain": chain,
        "applicable": applicable[:limit],
        "rejected": rejected[: limit * 2],
    }
