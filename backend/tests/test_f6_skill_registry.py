"""F6 — a step's tool is a registered thing with a contract, not a string.

`PlaybookStep.tool_ref` was declared in the schema, set by nothing and resolved
by nothing, so there was no way to ask what a step would invoke, what happens
when it times out, or whether running it twice is safe.

The registration invariants are the point of these tests. They are enforced at
the earliest place they can be — before a planner can select the skill, before
an approver can approve it, before an executor exists to run it.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from contextedge.models.skill import (
    IDEMPOTENCY_MODES,
    REPLAY_SAFE_MODES,
    ExecutionContract,
    Skill,
)
from contextedge.services.skill_registry_service import (
    SkillRegistryError,
    UnresolvedSkillReference,
    parse_tool_ref,
    resolve_skill,
    validate_contract,
    validate_skill,
    validate_step_bindings,
)


def _contract(**kwargs) -> ExecutionContract:
    defaults = dict(
        tenant_id=uuid.uuid4(),
        name="standard-api",
        idempotency_mode="NATIVE",
        timeout_sec=60,
        max_attempts=1,
        retry_backoff="none",
        supports_cancellation=False,
        supports_dry_run=False,
        concurrency_policy="parallel",
    )
    defaults.update(kwargs)
    return ExecutionContract(**defaults)


def _skill(**kwargs) -> Skill:
    defaults = dict(
        tenant_id=uuid.uuid4(),
        skill_key="restart_service",
        version="1.0.0",
        name="Restart a Windows service",
        interface_type="API",
        safety_class="read_only",
        status="draft",
        reversible=False,
        allowed_principal_roles=[],
    )
    defaults.update(kwargs)
    return Skill(**defaults)


# =========================================================================
# Registration invariants
# =========================================================================


def test_a_side_effecting_skill_needs_a_contract():
    """Without one it has no timeout, no retry policy and no statement about
    replay, and the executor would invent all three at call time."""
    for safety_class in ("low_side_effect", "high_side_effect", "destructive"):
        with pytest.raises(SkillRegistryError, match="needs an execution contract"):
            validate_skill(_skill(safety_class=safety_class), None)


def test_a_read_only_skill_may_register_without_a_contract():
    """Requiring one would push read-only diagnostics out of the registry, and
    an unregistered diagnostic is worse than an uncontracted one."""
    validate_skill(_skill(safety_class="read_only"), None)


def test_a_destructive_skill_may_not_claim_it_is_not_idempotent():
    """v6 invariant 8 at the earliest enforceable point: at-least-once
    delivery plus an unguarded side effect is how a remediation runs twice."""
    for safety_class in ("high_side_effect", "destructive"):
        with pytest.raises(SkillRegistryError, match="may not register as NOT_IDEMPOTENT"):
            validate_skill(
                _skill(safety_class=safety_class),
                _contract(idempotency_mode="NOT_IDEMPOTENT"),
            )


def test_the_same_skill_registers_with_a_replay_guarantee():
    """The tool is not blocked from the system — only from being registered as
    if replay were safe."""
    for mode in REPLAY_SAFE_MODES:
        contract = _contract(idempotency_mode=mode)
        if mode == "DEDUPE_ONLY":
            contract.deduplication_window_sec = 300
        validate_skill(_skill(safety_class="destructive"), contract)


def test_a_low_side_effect_skill_may_be_not_idempotent():
    """The guarantee is demanded where the blast radius justifies it, not
    everywhere — a rule that blocks everything gets switched off."""
    validate_skill(
        _skill(safety_class="low_side_effect"),
        _contract(idempotency_mode="NOT_IDEMPOTENT"),
    )


def test_dedupe_only_without_a_window_is_rejected():
    with pytest.raises(SkillRegistryError, match="requires deduplication_window_sec"):
        validate_contract(_contract(idempotency_mode="DEDUPE_ONLY"))
    validate_contract(
        _contract(idempotency_mode="DEDUPE_ONLY", deduplication_window_sec=300)
    )


def test_retries_without_a_replay_guarantee_are_rejected():
    """Retrying a call with no replay guarantee is how an action happens
    twice — the contract cannot declare both."""
    with pytest.raises(SkillRegistryError, match="retrying a call with no replay"):
        validate_contract(_contract(idempotency_mode="NOT_IDEMPOTENT", max_attempts=3))
    validate_contract(_contract(idempotency_mode="CALLER_KEY", max_attempts=3))


def test_vocabularies_are_validated_at_the_boundary():
    with pytest.raises(SkillRegistryError, match="interface_type"):
        validate_skill(_skill(interface_type="CARRIER_PIGEON"), None)
    with pytest.raises(SkillRegistryError, match="safety_class"):
        validate_skill(_skill(safety_class="mostly_harmless"), None)
    with pytest.raises(SkillRegistryError, match="action_type"):
        validate_skill(_skill(action_type="frobnication"), None)


def test_side_effect_class_reuses_the_executors_own_vocabulary():
    """A second vocabulary meaning the same thing is the drift this epic keeps
    closing, so the registry gates on SAFETY_CLASSES rather than v6's parallel
    sideEffectClassification names."""
    from contextedge.models.execution import SAFETY_CLASSES

    for safety_class in SAFETY_CLASSES:
        skill = _skill(safety_class=safety_class)
        contract = _contract(idempotency_mode="NATIVE")
        validate_skill(skill, contract)


def test_replay_safe_modes_are_a_subset_of_the_vocabulary():
    assert set(REPLAY_SAFE_MODES) < set(IDEMPOTENCY_MODES)
    assert "NOT_IDEMPOTENT" not in REPLAY_SAFE_MODES


# =========================================================================
# Resolution
# =========================================================================


def test_tool_ref_parses_pinned_and_unpinned_forms():
    assert parse_tool_ref("restart_service") == ("restart_service", None)
    assert parse_tool_ref("restart_service@2.0.0") == ("restart_service", "2.0.0")
    assert parse_tool_ref("  restart_service @ 2.0.0 ".replace(" @ ", "@")) == (
        "restart_service",
        "2.0.0",
    )


class _Result:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


@pytest.mark.asyncio
async def test_an_unknown_tool_ref_is_refused_not_ignored():
    db = SimpleNamespace(execute=AsyncMock(return_value=_Result(None)))
    with pytest.raises(UnresolvedSkillReference, match="no skill matches"):
        await resolve_skill(db, uuid.uuid4(), "restart_service")


@pytest.mark.asyncio
async def test_an_unpinned_reference_says_why_it_failed():
    """"No ACTIVE version" and "no such skill" are different problems and the
    author has to be able to tell them apart."""
    db = SimpleNamespace(execute=AsyncMock(return_value=_Result(None)))
    with pytest.raises(UnresolvedSkillReference, match="no ACTIVE version"):
        await resolve_skill(db, uuid.uuid4(), "restart_service")
    with pytest.raises(UnresolvedSkillReference) as pinned:
        await resolve_skill(db, uuid.uuid4(), "restart_service@9.9.9")
    assert "no ACTIVE version" not in str(pinned.value)


@pytest.mark.asyncio
async def test_an_empty_tool_ref_is_refused():
    db = SimpleNamespace(execute=AsyncMock(return_value=_Result(None)))
    with pytest.raises(UnresolvedSkillReference, match="empty"):
        await resolve_skill(db, uuid.uuid4(), "   ")


# =========================================================================
# The publish gate
# =========================================================================


@pytest.mark.asyncio
async def test_steps_naming_no_tool_are_left_alone():
    """Almost every playbook step today. Requiring a binding for them would
    block authoring rather than improve anything."""
    db = SimpleNamespace(execute=AsyncMock(return_value=_Result(None)))
    steps = [
        {"title": "Check the certificate expiry on vpn-gw-east-01"},
        {"title": "Renew it", "tool_ref": None},
        {"title": "Confirm with the reporter", "tool_ref": "   "},
        "a bare string step from a pre-M2 payload",
    ]
    assert await validate_step_bindings(db, uuid.uuid4(), steps) == {}
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_bound_step_resolves_and_reports_its_index():
    skill = _skill(status="active")
    db = SimpleNamespace(execute=AsyncMock(return_value=_Result(skill)))
    resolved = await validate_step_bindings(
        db, uuid.uuid4(), [{"title": "a"}, {"title": "b", "tool_ref": "restart_service"}]
    )
    assert resolved == {1: skill}


@pytest.mark.asyncio
async def test_an_unresolvable_binding_names_the_step():
    db = SimpleNamespace(execute=AsyncMock(return_value=_Result(None)))
    with pytest.raises(UnresolvedSkillReference, match="step 1:"):
        await validate_step_bindings(
            db, uuid.uuid4(), [{"title": "a"}, {"title": "b", "tool_ref": "nope"}]
        )
