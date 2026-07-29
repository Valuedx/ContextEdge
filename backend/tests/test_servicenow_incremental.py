"""ServiceNow incremental sync: keyset cursor, boundary seconds, retry."""

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from contextedge.connectors.base import Checkpoint
from contextedge.connectors.servicenow.connector import ServiceNowConnector


def _connector():
    return ServiceNowConnector(
        {"table": "incident"},
        {"instance_url": "https://acme.service-now.com", "username": "u", "password": "p"},
    )


@pytest.mark.asyncio
async def test_boundary_second_records_are_fetched_once():
    """A record sharing the checkpoint second but with a later sys_id is
    ingested; the exact already-seen record is skipped."""
    connector = _connector()
    connector._snow_get = AsyncMock(
        return_value={
            "result": [
                {"sys_id": "bbb", "sys_updated_on": "2026-07-29 10:00:00", "number": "INC2"},
                {"sys_id": "ccc", "sys_updated_on": "2026-07-29 10:00:01", "number": "INC3"},
            ]
        }
    )

    result = await connector.fetch_changes(
        "incident",
        "servicenow_table",
        Checkpoint(data={"last_updated": "2026-07-29 10:00:00", "last_sys_id": "aaa"}),
    )

    ids = [e.external_id for e in result.events]
    assert ids == ["bbb", "ccc"]
    assert result.new_checkpoint.data == {
        "last_updated": "2026-07-29 10:00:01",
        "last_sys_id": "ccc",
    }
    # Keyset query: strictly-after tuple with the ^NQ same-second branch,
    # stable tie-break ordering, and NO offset (offset pages over a
    # mutating table lose records).
    query = connector._snow_get.call_args[0][1]["sysparm_query"]
    assert "sys_updated_on>2026-07-29 10:00:00" in query
    assert "^NQsys_updated_on=2026-07-29 10:00:00^sys_id>aaa" in query
    assert "ORDERBYsys_id" in query
    assert "sysparm_offset" not in connector._snow_get.call_args[0][1]


@pytest.mark.asyncio
async def test_incremental_pages_advance_keyset_cursor():
    connector = _connector()
    page1 = {
        "result": [
            {"sys_id": f"id{i:03d}", "sys_updated_on": "2026-07-29 10:00:02"}
            for i in range(connector.INCREMENTAL_PAGE_SIZE)
        ]
    }
    page2 = {"result": [{"sys_id": "zzz", "sys_updated_on": "2026-07-29 10:00:03"}]}
    connector._snow_get = AsyncMock(side_effect=[page1, page2])

    result = await connector.fetch_changes(
        "incident",
        "servicenow_table",
        Checkpoint(data={"last_updated": "2026-07-29 10:00:00"}),
    )

    assert connector._snow_get.await_count == 2
    assert result.items_processed == connector.INCREMENTAL_PAGE_SIZE + 1
    assert result.new_checkpoint.data["last_sys_id"] == "zzz"
    # The second page must resume strictly after page 1's last tuple —
    # this is what makes >200 records in one second drain instead of loop.
    second_query = connector._snow_get.call_args_list[1][0][1]["sysparm_query"]
    last_id = f"id{connector.INCREMENTAL_PAGE_SIZE - 1:03d}"
    assert f"^NQsys_updated_on=2026-07-29 10:00:02^sys_id>{last_id}" in second_query


@pytest.mark.asyncio
async def test_legacy_checkpoint_without_sys_id_still_works():
    connector = _connector()
    connector._snow_get = AsyncMock(
        return_value={
            "result": [
                {"sys_id": "aaa", "sys_updated_on": "2026-07-29 10:00:05"},
            ]
        }
    )

    result = await connector.fetch_changes(
        "incident",
        "servicenow_table",
        Checkpoint(data={"last_updated": "2026-07-29 10:00:00"}),
    )

    assert [e.external_id for e in result.events] == ["aaa"]


# ---- retry / backoff ----


def _response(status_code, headers=None, payload=None):
    request = httpx.Request("GET", "https://acme.service-now.com/api/now/table/incident")
    return httpx.Response(
        status_code, headers=headers or {}, json=payload or {"result": []}, request=request
    )


class _FakeClient:
    """Minimal async-context httpx.AsyncClient stand-in fed by a queue."""

    responses: list[httpx.Response] = []
    calls = 0

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, *args, **kwargs):
        type(self).calls += 1
        return type(self).responses.pop(0)


@pytest.mark.asyncio
async def test_snow_get_retries_429_with_retry_after_then_succeeds():
    connector = _connector()
    _FakeClient.responses = [
        _response(429, headers={"Retry-After": "3"}),
        _response(200, payload={"result": [{"sys_id": "ok"}]}),
    ]
    _FakeClient.calls = 0
    sleeps: list[float] = []

    async def _fake_sleep(delay):
        sleeps.append(delay)

    with (
        patch("contextedge.connectors.servicenow.connector.httpx.AsyncClient", _FakeClient),
        patch("contextedge.connectors.servicenow.connector.asyncio.sleep", _fake_sleep),
    ):
        data = await connector._snow_get("/api/now/table/incident")

    assert data == {"result": [{"sys_id": "ok"}]}
    assert sleeps == [3.0]  # honored Retry-After, no real sleep


@pytest.mark.asyncio
async def test_snow_get_exhausts_retries_on_5xx_and_raises():
    connector = _connector()
    _FakeClient.responses = [_response(503), _response(503), _response(503)]
    _FakeClient.calls = 0

    async def _fake_sleep(delay):
        return None

    with (
        patch("contextedge.connectors.servicenow.connector.httpx.AsyncClient", _FakeClient),
        patch("contextedge.connectors.servicenow.connector.asyncio.sleep", _fake_sleep),
        pytest.raises(httpx.HTTPStatusError),
    ):
        await connector._snow_get("/api/now/table/incident")

    assert _FakeClient.calls == connector.MAX_ATTEMPTS


@pytest.mark.asyncio
async def test_snow_get_does_not_retry_auth_errors():
    connector = _connector()
    _FakeClient.responses = [_response(401)]
    _FakeClient.calls = 0

    with (
        patch("contextedge.connectors.servicenow.connector.httpx.AsyncClient", _FakeClient),
        pytest.raises(httpx.HTTPStatusError),
    ):
        await connector._snow_get("/api/now/table/incident")

    assert _FakeClient.calls == 1  # 4xx fails fast, no retries
