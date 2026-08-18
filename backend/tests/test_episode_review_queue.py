"""The ranked review queue: episodes ordered by what review unlocks.

Newest-first buried exactly the wrong drafts: after a bulk ingest the last
trickle of single-message fragments sat on page one while the
resolution-bearing, multi-evidence accounts — the ones patterns and
playbooks learn from — sat pages deep. `sort=review_priority` orders by a
deterministic SQL score instead; these tests pin the wiring and the factor
structure, not the database's arithmetic.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException
from sqlalchemy.dialects import postgresql

from contextedge.api.v1 import episodes

from .conftest import make_user


def _db_capturing_query():
    captured = {}
    scalars = Mock()
    scalars.all = Mock(return_value=[])
    result = Mock()
    result.scalars = Mock(return_value=scalars)

    async def execute(stmt, *a, **kw):
        captured["sql"] = str(stmt)
        return result

    return SimpleNamespace(execute=AsyncMock(side_effect=execute)), captured


@pytest.mark.asyncio
async def test_review_priority_orders_by_the_score_then_recency():
    db, captured = _db_capturing_query()
    await episodes.list_episodes(
        db=db, user=make_user(roles=["analyst"]),
        sort="review_priority", limit=50, offset=0,
    )
    sql = captured["sql"]
    order_by = sql[sql.index("ORDER BY"):]
    for factor in ("final_outcome", "root_cause_summary",
                   "jsonb_array_length", "extraction_confidence"):
        assert factor in order_by, f"{factor} missing from ordering"
    # Recency is the tie-break, not the ranking: it must come after the score.
    assert order_by.index("jsonb_array_length") < order_by.index("created_at")


@pytest.mark.asyncio
async def test_default_sort_is_unchanged_newest_first():
    db, captured = _db_capturing_query()
    await episodes.list_episodes(
        db=db, user=make_user(roles=["analyst"]), limit=50, offset=0,
    )
    order_by = captured["sql"][captured["sql"].index("ORDER BY"):]
    assert "created_at" in order_by
    assert "jsonb_array_length" not in order_by


@pytest.mark.asyncio
async def test_unknown_sort_is_rejected_not_ignored():
    db, _ = _db_capturing_query()
    with pytest.raises(HTTPException) as exc:
        await episodes.list_episodes(
            db=db, user=make_user(roles=["analyst"]),
            sort="cleverest_first", limit=50, offset=0,
        )
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_priority_sort_keeps_tenant_and_state_filters():
    """Ranking must never widen the result set — only reorder it."""
    db, captured = _db_capturing_query()
    await episodes.list_episodes(
        db=db, user=make_user(roles=["analyst"]),
        reviewer_state="pending_review", sort="review_priority",
        limit=50, offset=0,
    )
    assert "tenant_id" in captured["sql"]
    assert "reviewer_state" in captured["sql"]


def test_factor_weights_are_the_documented_ones():
    """The weights ARE the explanation (change-risk convention): outcome 40,
    root cause 20, evidence 3-per capped at 10, confidence x10. A silent
    weight change is a silent re-prioritization of human review time."""
    compiled = str(
        episodes._review_priority().compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "40" in compiled and "20" in compiled
    assert "least" in compiled and "* 3" in compiled
    assert "jsonb_typeof" in compiled  # non-array evidence_ids must not error
