"""The skill registry becomes authorable.

F6 built `Skill` + `ExecutionContract` with real validation and a resolver
for `PlaybookStep.tool_ref` — and nothing ever wrote a row. So `tool_ref`
resolved to nothing and an approved playbook had no way to say what to call.

These tests are about the two lifecycle rules the surface adds on top of the
service's own validation: a skill is born `draft`, and a rule change needs a
new version rather than an edit.
"""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from contextedge.api.v1 import skills as skills_api
from contextedge.services.skill_registry_service import SkillRegistryError

from .conftest import make_user


def _skill(tenant_id, *, status="draft", skill_id=None):
    return SimpleNamespace(
        id=skill_id or uuid.uuid4(), tenant_id=tenant_id, status=status,
        skill_key="restart_managed_server", version="1.0.0",
        name="Restart a WebLogic managed server", description=None,
    )


class _Result:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def scalars(self):
        return SimpleNamespace(all=lambda: ([] if self._value is None else [self._value]))


def _db(value=None):
    return SimpleNamespace(
        execute=AsyncMock(return_value=_Result(value)),
        commit=AsyncMock(),
        refresh=AsyncMock(),
    )


# =========================================================================
# A skill is born draft
# =========================================================================


def test_create_cannot_declare_a_status():
    """Registering something as immediately invocable skips the moment a
    human looks at what it can do and at what safety class — which is the
    point of having a registry rather than a string."""
    assert "status" not in set(skills_api.SkillCreate.model_fields)


@pytest.mark.asyncio
async def test_registering_a_skill_needs_the_admin_role():
    with pytest.raises(HTTPException) as excinfo:
        await skills_api.create_skill(
            body=skills_api.SkillCreate(
                skill_key="x", name="X", interface_type="API", safety_class="read_only"
            ),
            db=_db(), user=make_user(roles=["viewer"]),
        )
    assert excinfo.value.status_code == 403


@pytest.mark.asyncio
async def test_a_registration_the_service_refuses_is_a_400():
    """The service refuses a destructive skill with no replay guarantee. That
    is a request the caller can fix, not a server fault."""
    user = make_user(roles=["tenant_admin"])
    with patch.object(
        skills_api, "register_skill",
        AsyncMock(side_effect=SkillRegistryError("a destructive skill needs a contract")),
    ), pytest.raises(HTTPException) as excinfo:
        await skills_api.create_skill(
            body=skills_api.SkillCreate(
                skill_key="wipe", name="Wipe", interface_type="API",
                safety_class="destructive",
            ),
            db=_db(), user=user,
        )
    assert excinfo.value.status_code == 400
    assert "contract" in excinfo.value.detail


@pytest.mark.asyncio
async def test_a_registered_skill_is_committed_and_attributed():
    user = make_user(roles=["tenant_admin"])
    db = _db()
    registered = _skill(user.tenant_id)
    with patch.object(
        skills_api, "register_skill", AsyncMock(return_value=registered)
    ) as register:
        result = await skills_api.create_skill(
            body=skills_api.SkillCreate(
                skill_key="restart_managed_server", name="Restart",
                interface_type="WORKFLOW", safety_class="read_only",
            ),
            db=db, user=user,
        )
    assert result is registered
    assert register.await_args.kwargs["created_by"] == user.user_id
    db.commit.assert_awaited_once()


# =========================================================================
# Rules get a new version; labels get an edit
# =========================================================================


def test_only_labels_are_editable():
    """An active skill's endpoint or safety class cannot be mutated: a
    playbook was approved against those, and changing them under it rewrites
    what a past approval meant."""
    editable = set(skills_api.SkillUpdate.model_fields)
    assert editable == {"name", "description"}
    for field in ("endpoint_or_tool", "safety_class", "interface_type",
                  "execution_contract_id"):
        assert field not in editable
        assert field in skills_api.RULE_FIELDS


@pytest.mark.asyncio
async def test_a_label_edit_is_allowed():
    user = make_user(roles=["tenant_admin"])
    skill = _skill(user.tenant_id, status="active")
    db = _db(skill)
    await skills_api.update_skill(
        skill_id=skill.id, body=skills_api.SkillUpdate(name="Restart (prod)"),
        db=db, user=user,
    )
    assert skill.name == "Restart (prod)"
    db.commit.assert_awaited_once()


# =========================================================================
# Lifecycle
# =========================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("current", "target"),
    [("draft", "active"), ("active", "deprecated"), ("deprecated", "active"),
     ("active", "retired")],
)
async def test_allowed_transitions(current, target):
    user = make_user(roles=["tenant_admin"])
    skill = _skill(user.tenant_id, status=current)
    await skills_api.change_skill_status(
        skill_id=skill.id, body=skills_api.SkillStatusChange(status=target),
        db=_db(skill), user=user,
    )
    assert skill.status == target


@pytest.mark.asyncio
async def test_retirement_is_one_way():
    """Bringing back something a human retired should cost a new version, so
    the decision stays legible."""
    user = make_user(roles=["tenant_admin"])
    skill = _skill(user.tenant_id, status="retired")
    with pytest.raises(HTTPException) as excinfo:
        await skills_api.change_skill_status(
            skill_id=skill.id, body=skills_api.SkillStatusChange(status="active"),
            db=_db(skill), user=user,
        )
    assert excinfo.value.status_code == 409
    assert skill.status == "retired"


@pytest.mark.asyncio
async def test_a_draft_cannot_skip_straight_to_deprecated():
    user = make_user(roles=["tenant_admin"])
    skill = _skill(user.tenant_id, status="draft")
    with pytest.raises(HTTPException) as excinfo:
        await skills_api.change_skill_status(
            skill_id=skill.id, body=skills_api.SkillStatusChange(status="deprecated"),
            db=_db(skill), user=user,
        )
    assert excinfo.value.status_code == 409


@pytest.mark.asyncio
async def test_an_unknown_status_is_refused():
    user = make_user(roles=["tenant_admin"])
    skill = _skill(user.tenant_id)
    with pytest.raises(HTTPException) as excinfo:
        await skills_api.change_skill_status(
            skill_id=skill.id, body=skills_api.SkillStatusChange(status="live"),
            db=_db(skill), user=user,
        )
    assert excinfo.value.status_code == 400


@pytest.mark.asyncio
async def test_another_tenants_skill_is_not_found():
    user = make_user(roles=["tenant_admin"])
    with pytest.raises(HTTPException) as excinfo:
        await skills_api.change_skill_status(
            skill_id=uuid.uuid4(), body=skills_api.SkillStatusChange(status="active"),
            db=_db(None), user=user,
        )
    assert excinfo.value.status_code == 404


@pytest.mark.asyncio
async def test_listing_rejects_an_unknown_status_filter():
    """An empty list and a typo look identical to the caller."""
    with pytest.raises(HTTPException) as excinfo:
        await skills_api.list_skills(
            db=_db(), user=make_user(roles=["viewer"]),
            skill_key=None, skill_status="enabled", action_type=None, limit=100,
        )
    assert excinfo.value.status_code == 400


def test_the_registry_is_routed():
    """An authoring surface nobody can reach is the same gap one layer up."""
    import inspect

    from contextedge.api import v1

    paths = {route.path for route in skills_api.router.routes}
    assert paths == {"", "/execution-contracts", "/{skill_id}", "/{skill_id}/status"}

    mounted = inspect.getsource(v1)
    assert 'skills.router, prefix="/skills"' in mounted
