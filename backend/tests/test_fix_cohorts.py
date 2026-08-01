"""B5 cohort stats + promotion policy: counting, the ladder, failure
narrowing, and the reviewer gate."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from contextedge.models.fix_applicability import FixApplicabilityRule
from contextedge.models.fix_cohort import FixCohortStat
from contextedge.services.fix_cohort_service import (
    PROMOTION_CREATED_BY,
    cohorts_for_entity,
    evaluate_promotions,
    record_fix_outcome,
)


class _NestedTx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


def _entity(model=None, ci_class="cmdb_ci_computer"):
    return SimpleNamespace(
        id=uuid4(),
        model=model,
        attributes={"ci_class": ci_class},
    )


def _laptop_chain_db(stats=None, rules=None, fix=None, added=None):
    """Fake with entity_classes chain endpoint -> computing_device."""
    added = added if added is not None else []
    endpoint = SimpleNamespace(
        id=uuid4(), canonical_key="endpoint", parent_class_id=uuid4()
    )
    computing = SimpleNamespace(
        id=endpoint.parent_class_id, canonical_key="computing_device",
        parent_class_id=None,
    )

    async def execute(stmt):
        text = str(stmt)
        result = Mock()
        if text.startswith("SELECT entity_classes."):
            result.scalar_one_or_none.return_value = endpoint
            return result
        if text.startswith("SELECT fix_cohort_stats.") and "cohort_type =" in text:
            # single-stat lookup during recording
            result.scalar_one_or_none.return_value = None
            return result
        if text.startswith("SELECT fix_cohort_stats."):
            result.scalars.return_value.all.return_value = list(stats or [])
            return result
        if text.startswith("SELECT fix_applicability_rules."):
            result.scalars.return_value.all.return_value = list(rules or [])
            return result
        result.scalar_one_or_none.return_value = None
        result.scalars.return_value.all.return_value = []
        return result

    async def get(model_cls, pk):
        if pk == endpoint.parent_class_id:
            return computing
        return fix

    return SimpleNamespace(
        execute=execute,
        get=AsyncMock(side_effect=get),
        add=added.append,
        flush=AsyncMock(),
        begin_nested=Mock(return_value=_NestedTx()),
    ), added


@pytest.mark.asyncio
async def test_cohorts_resolve_at_three_grains_absent_model_absent():
    db, _ = _laptop_chain_db()
    with_model = await cohorts_for_entity(db, _entity(model="Latitude 5420"))
    assert ("model", "Latitude 5420") in with_model
    assert ("class", "endpoint") in with_model
    assert ("family", "computing_device") in with_model

    without_model = await cohorts_for_entity(db, _entity())
    assert all(t != "model" for t, _k in without_model)


def _stat(fix_id, cohort_type, key, ok=0, bad=0):
    return FixCohortStat(
        tenant_id=uuid4(),
        fix_pattern_id=fix_id,
        cohort_type=cohort_type,
        cohort_key=key,
        success_count=ok,
        failure_count=bad,
    )


@pytest.mark.asyncio
async def test_two_model_successes_mint_review_gated_candidate():
    tenant_id = uuid4()
    fix_id = uuid4()
    stats = [_stat(fix_id, "model", "Latitude 5420", ok=2)]
    db, added = _laptop_chain_db(stats=stats)

    created = await evaluate_promotions(db, tenant_id, fix_id)

    assert created == 1
    (rule,) = [a for a in added if isinstance(a, FixApplicabilityRule)]
    assert rule.required_traits == {"model": "Latitude 5420"}
    assert rule.applicability_level == "same_model_and_configuration"
    assert rule.approval_requirement == "review"  # the reviewer gate
    assert rule.created_by == PROMOTION_CREATED_BY


@pytest.mark.asyncio
async def test_one_success_is_a_precedent_not_a_rule():
    tenant_id = uuid4()
    fix_id = uuid4()
    db, added = _laptop_chain_db(stats=[_stat(fix_id, "model", "Latitude 5420", ok=1)])
    assert await evaluate_promotions(db, tenant_id, fix_id) == 0
    assert added == []


@pytest.mark.asyncio
async def test_two_proven_models_mint_class_candidate():
    tenant_id = uuid4()
    fix_id = uuid4()
    stats = [
        _stat(fix_id, "model", "Latitude 5420", ok=2),
        _stat(fix_id, "model", "EliteBook 840", ok=3),
        _stat(fix_id, "class", "endpoint", ok=5),
    ]
    db, added = _laptop_chain_db(stats=stats)

    created = await evaluate_promotions(db, tenant_id, fix_id)

    rules = [a for a in added if isinstance(a, FixApplicabilityRule)]
    class_rules = [r for r in rules if r.target_class_key == "endpoint"]
    assert len(class_rules) == 1
    assert class_rules[0].applicability_level == "same_ci_class"
    assert class_rules[0].approval_requirement == "review"
    assert created == len(rules)


@pytest.mark.asyncio
async def test_failures_block_their_cohort_and_broader():
    """Works on laptops, fails on desktops -> applicability stays
    narrow automatically; no class or family candidate is minted."""
    tenant_id = uuid4()
    fix_id = uuid4()
    stats = [
        _stat(fix_id, "model", "Latitude 5420", ok=4),
        _stat(fix_id, "model", "OptiPlex 7000", ok=0, bad=2),
        _stat(fix_id, "class", "endpoint", ok=4, bad=2),  # failures present
        _stat(fix_id, "family", "computing_device", ok=4, bad=2),
    ]
    db, added = _laptop_chain_db(stats=stats)

    await evaluate_promotions(db, tenant_id, fix_id)

    rules = [a for a in added if isinstance(a, FixApplicabilityRule)]
    # Only the proven model candidate exists; nothing broader.
    assert len(rules) == 1
    assert rules[0].required_traits == {"model": "Latitude 5420"}


@pytest.mark.asyncio
async def test_promotion_is_idempotent():
    tenant_id = uuid4()
    fix_id = uuid4()
    existing_rule = SimpleNamespace(
        target_class_key=None,
        required_traits={"model": "Latitude 5420"},
        created_by=PROMOTION_CREATED_BY,
    )
    db, added = _laptop_chain_db(
        stats=[_stat(fix_id, "model", "Latitude 5420", ok=5)],
        rules=[existing_rule],
    )
    assert await evaluate_promotions(db, tenant_id, fix_id) == 0
    assert added == []


@pytest.mark.asyncio
async def test_record_outcome_updates_counters_at_each_grain():
    tenant_id = uuid4()
    fix = SimpleNamespace(
        id=uuid4(), tenant_id=tenant_id, success_count=0, failure_count=0
    )
    db, added = _laptop_chain_db(fix=fix)

    result = await record_fix_outcome(
        db, tenant_id, fix.id, _entity(model="Latitude 5420"), True
    )

    assert result["cohorts"] == 3  # model + class + family
    assert fix.success_count == 1
    stats = [a for a in added if isinstance(a, FixCohortStat)]
    assert {s.cohort_type for s in stats} == {"model", "class", "family"}
    assert all(s.success_count == 1 and s.failure_count == 0 for s in stats)
