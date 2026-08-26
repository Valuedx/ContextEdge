from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from contextedge.search.pg_fts import search_evidence_fts, search_playbooks_fts


def _all_result(values):
    result = Mock()
    result.all.return_value = values
    return result


@pytest.mark.asyncio
async def test_search_evidence_fts_uses_stored_tsvector():
    tenant_id = uuid4()
    captured = {}

    async def execute(stmt):
        captured["sql"] = str(stmt.compile(dialect=postgresql.dialect()))
        return _all_result([])

    db = SimpleNamespace(execute=AsyncMock(side_effect=execute))

    await search_evidence_fts(db, tenant_id, "database timeout", limit=10)

    assert "search_tsvector" in captured["sql"]
    assert "to_tsvector" not in captured["sql"]


@pytest.mark.asyncio
async def test_search_playbooks_fts_uses_stored_tsvector():
    tenant_id = uuid4()
    captured = {}

    async def execute(stmt):
        captured["sql"] = str(stmt.compile(dialect=postgresql.dialect()))
        return _all_result([])

    db = SimpleNamespace(execute=AsyncMock(side_effect=execute))

    await search_playbooks_fts(db, tenant_id, "restart service", limit=5)

    assert "search_tsvector" in captured["sql"]
    assert "to_tsvector" not in captured["sql"]
    assert "websearch_to_tsquery" in captured["sql"]
    assert "plainto_tsquery" not in captured["sql"]


@pytest.mark.asyncio
async def test_search_playbooks_fts_empty_query_skips_db():
    db = SimpleNamespace(execute=AsyncMock())
    assert await search_playbooks_fts(db, uuid4(), "   ", limit=5) == []
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_search_evidence_fts_or_compose_uses_websearch():
    tenant_id = uuid4()
    captured = {}

    async def execute(stmt):
        captured["sql"] = str(stmt.compile(dialect=postgresql.dialect()))
        return _all_result([])

    db = SimpleNamespace(execute=AsyncMock(side_effect=execute))
    await search_evidence_fts(
        db,
        tenant_id,
        "sharepoint excel download fails in the workflow export",
        compose="or",
    )
    assert "websearch_to_tsquery" in captured["sql"]
    assert "plainto_tsquery" not in captured["sql"]
