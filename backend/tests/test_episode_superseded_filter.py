"""Superseded episodes must not be served as though they were current.

Reconstruction replaces its own drafts as more of a thread arrives, so a
superseded row is by definition the version that was replaced -- 138 of
253 on the live graph, 54%. Returned alongside current episodes with
nothing but a status badge to distinguish them, they are
indistinguishable from the answer.

The replaced draft is usually the worse one. The ActiveMQ draft that
prompted this conflated two separate incidents -- a service stopped after
a patching reboot, and memory-pressure unresponsiveness -- and recorded
two complaints with no remediation. Its replacement recorded the broker
bounce and the seven in-flight executions that had to be resubmitted.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from .conftest import make_user
from contextedge.api.v1 import episodes


def _db_capturing_query():
    """A db whose execute() records the statement it was handed."""
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
async def test_superseded_episodes_are_excluded_by_default():
    db, captured = _db_capturing_query()
    await episodes.list_episodes(db=db, user=make_user(roles=["analyst"]), limit=50, offset=0)
    assert "reviewer_state" in captured["sql"]
    assert "!=" in captured["sql"] or "IS NOT" in captured["sql"]


@pytest.mark.asyncio
async def test_superseded_can_still_be_requested_explicitly():
    """A reviewer auditing what changed needs to see the replaced draft."""
    db, captured = _db_capturing_query()
    await episodes.list_episodes(
        db=db, user=make_user(roles=["analyst"]), include_superseded=True,
        limit=50, offset=0,
    )
    assert "!=" not in captured["sql"]


@pytest.mark.asyncio
async def test_naming_the_state_wins_over_the_default_exclusion():
    """Filtering FOR superseded must not be silently emptied by the
    default that excludes it — that would return nothing and look like
    there were none, which is the opposite of the truth."""
    db, captured = _db_capturing_query()
    await episodes.list_episodes(
        db=db, user=make_user(roles=["analyst"]), reviewer_state="superseded",
        limit=50, offset=0,
    )
    sql = captured["sql"]
    assert "=" in sql
    assert "!=" not in sql


@pytest.mark.asyncio
async def test_other_filters_still_apply_alongside_the_exclusion():
    db, captured = _db_capturing_query()
    await episodes.list_episodes(
        db=db, user=make_user(roles=["analyst"]), status="approved",
        limit=50, offset=0,
    )
    sql = captured["sql"]
    assert "status" in sql
    assert "reviewer_state" in sql


@pytest.mark.asyncio
async def test_the_tenant_scope_is_never_dropped():
    """The exclusion is an added predicate, not a replacement for the one
    that keeps tenants apart."""
    db, captured = _db_capturing_query()
    await episodes.list_episodes(db=db, user=make_user(roles=["analyst"]), limit=50, offset=0)
    assert "tenant_id" in captured["sql"]
