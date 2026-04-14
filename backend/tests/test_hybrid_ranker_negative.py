from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from contextedge.search.hybrid_ranker import _negative_penalty_for_playbook


class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar(self):
        return self._value


@pytest.mark.asyncio
async def test_negative_penalty_no_contradictions():
    tenant_id = uuid4()
    db = SimpleNamespace(
        execute=AsyncMock(side_effect=[
            _ScalarResult(0),   # contradiction count
            _ScalarResult(0),   # negative knowledge count
        ]),
    )

    score = await _negative_penalty_for_playbook(db, tenant_id, uuid4(), uuid4())
    assert score == 0.0


@pytest.mark.asyncio
async def test_negative_penalty_with_contradictions():
    tenant_id = uuid4()
    db = SimpleNamespace(
        execute=AsyncMock(side_effect=[
            _ScalarResult(2),   # 2 contradiction edges
            _ScalarResult(0),   # no negative knowledge
        ]),
    )

    score = await _negative_penalty_for_playbook(db, tenant_id, uuid4(), uuid4())
    assert score == pytest.approx(0.6)  # 2 * 0.3


@pytest.mark.asyncio
async def test_negative_penalty_caps_at_one():
    tenant_id = uuid4()
    db = SimpleNamespace(
        execute=AsyncMock(side_effect=[
            _ScalarResult(5),   # 5 contradictions -> 1.5 raw
            _ScalarResult(10),  # 10 NK items -> 1.0 raw; total 2.5
        ]),
    )

    score = await _negative_penalty_for_playbook(db, tenant_id, uuid4(), uuid4())
    assert score == 1.0


@pytest.mark.asyncio
async def test_negative_penalty_no_domain_skips_nk():
    """When domain_id is None, negative knowledge lookup is skipped."""
    tenant_id = uuid4()
    db = SimpleNamespace(
        execute=AsyncMock(side_effect=[
            _ScalarResult(1),   # 1 contradiction
        ]),
    )

    score = await _negative_penalty_for_playbook(db, tenant_id, uuid4(), domain_id=None)
    assert score == pytest.approx(0.3)
    assert db.execute.await_count == 1


@pytest.mark.asyncio
async def test_negative_penalty_mixed():
    tenant_id = uuid4()
    db = SimpleNamespace(
        execute=AsyncMock(side_effect=[
            _ScalarResult(1),   # 1 contradiction -> 0.3
            _ScalarResult(3),   # 3 NK items -> 0.3; total 0.6
        ]),
    )

    score = await _negative_penalty_for_playbook(db, tenant_id, uuid4(), uuid4())
    assert score == pytest.approx(0.6)
