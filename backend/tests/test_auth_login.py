from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from contextedge.api.v1.auth import login, pwd_context
from contextedge.schemas.tenant import LoginRequest


class _ScalarsResult:
    def __init__(self, values):
        self._values = values

    def scalars(self):
        return SimpleNamespace(all=lambda: list(self._values))

    def all(self):
        return [(v,) for v in self._values]


def _user(username="ops-acme", password="pw", status="active", tenant_id=None):
    return SimpleNamespace(
        id=uuid4(),
        tenant_id=tenant_id or uuid4(),
        username=username,
        email=None,
        password_hash=pwd_context.hash(password),
        status=status,
    )


def _db(users, roles=("analyst",)):
    captured = []

    async def _execute(stmt, *args, **kwargs):
        captured.append(stmt)
        s = str(stmt)
        if "users" in s:
            return _ScalarsResult(users)
        if "role_bindings" in s:
            return _ScalarsResult([SimpleNamespace(role=r, scope_type="tenant", scope_id=None) for r in roles])
        return _ScalarsResult([])

    return SimpleNamespace(execute=_execute), captured


def _request(headers=None, client_host="203.0.113.9"):
    raw = []
    for key, value in (headers or {}).items():
        raw.append((key.lower().encode("latin-1"), str(value).encode("latin-1")))
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/login",
        "raw_path": b"/login",
        "query_string": b"",
        "headers": raw,
        "client": (client_host, 4321),
        "server": ("testserver", 80),
    }
    return Request(scope)


@pytest.fixture(autouse=True)
def login_events(monkeypatch):
    events = []

    async def capture(**kwargs):
        events.append(kwargs)

    monkeypatch.setattr("contextedge.api.v1.auth._persist_login_event", capture)
    return events


@pytest.mark.asyncio
async def test_login_success_returns_token():
    user = _user()
    db, captured = _db([user])

    response = await login(LoginRequest(username="ops-acme", password="pw"), db, _request())

    assert response.access_token
    # The user query must filter on active status at the SQL layer.
    assert any("users.status" in str(s) for s in captured)


@pytest.mark.asyncio
async def test_login_wrong_password_rejected():
    db, _ = _db([_user(password="correct")])

    with pytest.raises(HTTPException) as exc:
        await login(LoginRequest(username="ops-acme", password="wrong"), db, _request())
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_login_duplicate_username_does_not_500():
    """Same username colliding in the query set must not leak as 500."""
    db, _ = _db([_user(password="pw"), _user(password="other")])

    response = await login(LoginRequest(username="ops-acme", password="pw"), db, _request())
    assert response.access_token


@pytest.mark.asyncio
async def test_login_ambiguous_same_password_rejected():
    db, _ = _db([_user(password="pw"), _user(password="pw")])

    with pytest.raises(HTTPException) as exc:
        await login(LoginRequest(username="ops-acme", password="pw"), db, _request())
    assert exc.value.status_code == 401
    assert "Ambiguous" in exc.value.detail


@pytest.mark.asyncio
async def test_login_user_without_password_hash_rejected():
    user = _user()
    user.password_hash = None
    db, _ = _db([user])

    with pytest.raises(HTTPException) as exc:
        await login(LoginRequest(username="ops-acme", password="pw"), db, _request())
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_login_success_records_extension_event(login_events):
    user = _user()
    db, _ = _db([user])
    request = _request(
        {
            "X-Client": "extension",
            "X-Extension-Version": "0.4.0",
            "X-Forwarded-For": "198.51.100.10",
            "User-Agent": "Chrome",
        }
    )

    await login(LoginRequest(username="ops-acme", password="pw"), db, request)

    assert login_events
    event = login_events[-1]
    assert event["success"] is True
    assert event["user_id"] == user.id
    assert event["tenant_id"] == user.tenant_id
    assert event["client"] == "extension"
    assert event["extension_version"] == "0.4.0"
    assert event["ip_address"] == "198.51.100.10"


@pytest.mark.asyncio
async def test_login_failure_records_event(login_events):
    db, _ = _db([_user(password="correct")])

    with pytest.raises(HTTPException):
        await login(
            LoginRequest(username="ops-acme", password="wrong"),
            db,
            _request({"X-Client": "extension"}),
        )

    assert login_events
    event = login_events[-1]
    assert event["success"] is False
    assert event["failure_reason"] == "invalid_credentials"
    assert event["client"] == "extension"
