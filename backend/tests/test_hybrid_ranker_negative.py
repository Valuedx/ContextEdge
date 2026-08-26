from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from contextedge.search.hybrid_ranker import _negative_penalty_for_playbook


class _AllResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


@pytest.mark.asyncio
async def test_negative_penalty_no_contradictions():
    playbook_id = uuid4()
    db = SimpleNamespace(execute=AsyncMock(return_value=_AllResult([])))
    score = await _negative_penalty_for_playbook(db, uuid4(), playbook_id, uuid4())
    assert score == 0.0


def _graph_then_nk(graph_rows, nk_rows=None):
    """Contradiction counts issue two queries: graph edges, then NK links."""
    return AsyncMock(
        side_effect=[_AllResult(graph_rows), _AllResult(nk_rows or [])]
    )


@pytest.mark.asyncio
async def test_negative_penalty_with_contradictions():
    playbook_id = uuid4()
    db = SimpleNamespace(execute=_graph_then_nk([(playbook_id, 2)]))
    score = await _negative_penalty_for_playbook(db, uuid4(), playbook_id, uuid4())
    assert score == pytest.approx(0.6)


@pytest.mark.asyncio
async def test_negative_penalty_caps_at_one():
    playbook_id = uuid4()
    db = SimpleNamespace(execute=_graph_then_nk([(playbook_id, 5)]))
    score = await _negative_penalty_for_playbook(db, uuid4(), playbook_id, uuid4())
    assert score == 1.0


@pytest.mark.asyncio
async def test_negative_penalty_is_not_domain_wide_constant():
    """G2.5: domain-wide negative knowledge is not a constant penalty.

    Domain_id is ignored; only contradiction edges on this playbook count.
    """
    playbook_id = uuid4()
    db = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                _AllResult([(playbook_id, 1)]),
                _AllResult([]),
                _AllResult([(playbook_id, 1)]),
                _AllResult([]),
            ]
        )
    )
    with_domain = await _negative_penalty_for_playbook(
        db, uuid4(), playbook_id, uuid4()
    )
    without_domain = await _negative_penalty_for_playbook(
        db, uuid4(), playbook_id, domain_id=None
    )
    assert with_domain == pytest.approx(without_domain)
    assert with_domain == pytest.approx(0.3)
    # Two queries per call (graph edges + NK links) × two calls.
    assert db.execute.await_count == 4
