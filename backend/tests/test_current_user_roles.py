from uuid import uuid4

from contextedge.deps import CurrentUser


def test_tenant_admin_is_not_platform_super_admin():
    user = CurrentUser(
        user_id=uuid4(),
        tenant_id=uuid4(),
        email="ta",
        roles=["tenant_admin"],
    )
    assert user.has_role("tenant_admin")
    assert user.has_role("knowledge_manager")
    assert user.has_role("domain_admin")
    assert not user.has_role("platform_super_admin")
    assert not user.has_exact_role("platform_super_admin")


def test_platform_super_admin_has_all_roles():
    user = CurrentUser(
        user_id=uuid4(),
        tenant_id=uuid4(),
        email="sa",
        roles=["platform_super_admin"],
    )
    assert user.has_role("platform_super_admin")
    assert user.has_role("tenant_admin")
    assert user.has_exact_role("platform_super_admin")


def test_analyst_does_not_inherit_admin_roles():
    user = CurrentUser(
        user_id=uuid4(),
        tenant_id=uuid4(),
        email="a",
        roles=["analyst"],
    )
    assert user.has_role("analyst")
    assert not user.has_role("domain_admin")
    assert not user.has_role("tenant_admin")
