from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from contextedge.api.v1.evidence import (
    get_live_ticket_context_by_source,
    get_live_zoho_ticket_context,
)
from .conftest import make_user


class FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class FakeDb:
    def __init__(self, values):
        self._values = list(values)

    async def execute(self, *args, **kwargs):
        return FakeResult(self._values.pop(0))


@pytest.mark.asyncio
async def test_zoho_alias_still_rejects_non_digit_ids():
    user = make_user(roles=["domain_admin"])
    with pytest.raises(HTTPException) as exc:
        await get_live_zoho_ticket_context("SUP-4821", FakeDb([]), user)
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_by_source_rejects_an_oversized_id():
    user = make_user(roles=["domain_admin"])
    with pytest.raises(HTTPException) as exc:
        await get_live_ticket_context_by_source(
            "zoho_desk", "x" * 65, FakeDb([]), user
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_by_source_returns_501_when_the_connector_has_no_fetch():
    user = make_user(roles=["domain_admin"])
    source = SimpleNamespace(
        id=uuid4(),
        source_type="servicenow",
        config={},
        tenant_id=user.tenant_id,
    )
    credential = SimpleNamespace(encrypted_credentials=b"enc")
    db = FakeDb([source, credential])

    class NoFetch:
        pass

    with patch(
        "contextedge.services.source_service.decrypt_credentials",
        AsyncMock(return_value={}),
    ), patch(
        "contextedge.connectors.registry.get_connector",
        Mock(return_value=NoFetch()),
    ):
        with pytest.raises(HTTPException) as exc:
            await get_live_ticket_context_by_source(
                "servicenow", "a" * 32, db, user
            )
    assert exc.value.status_code == 501


@pytest.mark.asyncio
async def test_by_source_dispatches_to_fetch_ticket_context():
    user = make_user(roles=["domain_admin"])
    source = SimpleNamespace(
        id=uuid4(),
        source_type="zoho_desk",
        config={},
        tenant_id=user.tenant_id,
    )
    credential = SimpleNamespace(encrypted_credentials=b"enc")
    db = FakeDb([source, credential])

    class Live:
        async def fetch_ticket_context(self, ticket_id):
            return {"ticket": {"id": ticket_id}, "messages": []}

    with patch(
        "contextedge.services.source_service.decrypt_credentials",
        AsyncMock(return_value={"token": "x"}),
    ), patch(
        "contextedge.connectors.registry.get_connector",
        Mock(return_value=Live()),
    ):
        result = await get_live_ticket_context_by_source(
            "zoho_desk", "11270000099963067", db, user
        )
    assert result["ticket"]["id"] == "11270000099963067"


@pytest.mark.asyncio
async def test_zoho_alias_delegates_to_the_shared_helper():
    user = make_user(roles=["domain_admin"])
    source = SimpleNamespace(
        id=uuid4(),
        source_type="zoho_desk",
        config={},
        tenant_id=user.tenant_id,
    )
    credential = SimpleNamespace(encrypted_credentials=b"enc")
    db = FakeDb([source, credential])

    class Live:
        async def fetch_ticket_context(self, ticket_id):
            return {"ticket": {"id": ticket_id}}

    with patch(
        "contextedge.services.source_service.decrypt_credentials",
        AsyncMock(return_value={}),
    ), patch(
        "contextedge.connectors.registry.get_connector",
        Mock(return_value=Live()),
    ):
        result = await get_live_zoho_ticket_context("11270000099963067", db, user)
    assert result["ticket"]["id"] == "11270000099963067"


@pytest.mark.asyncio
async def test_by_source_requires_domain_admin():
    user = make_user(roles=["analyst"])
    with pytest.raises(HTTPException) as exc:
        await get_live_ticket_context_by_source("zoho_desk", "1", FakeDb([]), user)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_by_source_404_when_source_is_missing():
    user = make_user(roles=["domain_admin"])
    with pytest.raises(HTTPException) as exc:
        await get_live_ticket_context_by_source("zoho_desk", "1", FakeDb([None]), user)
    assert exc.value.status_code == 404
