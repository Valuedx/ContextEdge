from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import pytest

from .conftest import make_user
from contextedge.api.v1 import evidence


@pytest.mark.asyncio
async def test_evidence_search_with_query():
    user = make_user()
    db = SimpleNamespace()
    item_one = SimpleNamespace(id=uuid4(), title="first")
    item_two = SimpleNamespace(id=uuid4(), title="second")

    with patch(
        "contextedge.search.pg_fts.search_evidence_fts",
        AsyncMock(return_value=[(item_one, 0.9), (item_two, 0.7)]),
    ) as search_mock:
        with patch(
            "contextedge.api.v1.evidence.resolve_excluded_access_policy_ids",
            AsyncMock(return_value=None),
        ):
            result = await evidence.search_evidence(
                db=db,
                user=user,
                query="foo",
                limit=5,
            )

    assert result == [item_one, item_two]
    search_mock.assert_awaited_once_with(
        db,
        user.tenant_id,
        "foo",
        limit=5,
        exclude_policy_ids=None,
        # Filter passthrough added 2026-08-07 (state/type/source facets).
        relevance_state=None,
        evidence_type=None,
        source_type=None,
    )


@pytest.mark.asyncio
async def test_evidence_search_without_query():
    user = make_user()
    items = [SimpleNamespace(id=uuid4()), SimpleNamespace(id=uuid4())]
    db_result = Mock()
    db_result.scalars.return_value.all.return_value = items
    db = SimpleNamespace(execute=AsyncMock(return_value=db_result))

    with patch(
        "contextedge.api.v1.evidence.resolve_excluded_access_policy_ids",
        AsyncMock(return_value=None),
    ):
        result = await evidence.search_evidence(db=db, user=user, limit=50, offset=0)

    assert result == items
    db.execute.assert_awaited_once()
