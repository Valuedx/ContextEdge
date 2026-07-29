"""ServiceNow incremental sync: compound checkpoint, boundary seconds, paging."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

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
                {"sys_id": "aaa", "sys_updated_on": "2026-07-29 10:00:00", "number": "INC1"},
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
    # Query uses >= with stable tie-break ordering.
    query = connector._snow_get.call_args[0][1]["sysparm_query"]
    assert "sys_updated_on>=" in query
    assert "ORDERBYsys_id" in query


@pytest.mark.asyncio
async def test_incremental_pages_until_short_page():
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
