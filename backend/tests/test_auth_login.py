from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from contextedge.api.v1.auth import login, pwd_context
from contextedge.schemas.tenant import LoginRequest


class _ScalarsResult:
    def __init__(self, values):
        self._values = values

    def scalars(self):
        return SimpleNamespace(all=lambda: list(self._values))

    def all(self):
        return [(v,) for v in self._values]


def _user(email="ops@acme.com", password="pw", status="active", tenant_id=None):
    return SimpleNamespace(
        id=uuid4(),
        tenant_id=tenant_id or uuid4(),
        email=email,
        password_hash=pwd_context.hash(password),
        status=status,
    )


def _db(users, roles=("analyst",)):
    captured = []

    async def _execute(stmt):
        captured.append(stmt)
        if len(captured) == 1:
            return _ScalarsResult(users)
        return _ScalarsResult(list(roles))

    return SimpleNamespace(execute=_execute), captured


@pytest.mark.asyncio
async def test_login_success_returns_token():
    user = _user()
    db, captured = _db([user])

    response = await login(LoginRequest(email="ops@acme.com", password="pw"), db)

    assert response.access_token
    # The user query must filter on active status at the SQL layer.
    assert "users.status" in str(captured[0])


@pytest.mark.asyncio
async def test_login_wrong_password_rejected():
    db, _ = _db([_user(password="correct")])

    with pytest.raises(HTTPException) as exc:
        await login(LoginRequest(email="ops@acme.com", password="wrong"), db)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_login_duplicate_email_does_not_500():
    """Same email in two tenants: MultipleResultsFound must not leak as 500."""
    db, _ = _db([_user(password="pw"), _user(password="other")])

    response = await login(LoginRequest(email="ops@acme.com", password="pw"), db)
    assert response.access_token


@pytest.mark.asyncio
async def test_login_ambiguous_same_password_rejected():
    db, _ = _db([_user(password="pw"), _user(password="pw")])

    with pytest.raises(HTTPException) as exc:
        await login(LoginRequest(email="ops@acme.com", password="pw"), db)
    assert exc.value.status_code == 401
    assert "Ambiguous" in exc.value.detail


@pytest.mark.asyncio
async def test_login_user_without_password_hash_rejected():
    user = _user()
    user.password_hash = None
    db, _ = _db([user])

    with pytest.raises(HTTPException) as exc:
        await login(LoginRequest(email="ops@acme.com", password="pw"), db)
    assert exc.value.status_code == 401
