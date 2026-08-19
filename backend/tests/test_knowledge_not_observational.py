"""An operational episode needs at least one observational source.

A cluster made only of knowledge — KB articles, SOPs — describes what a
document claims works. Narrating it as an episode turns "this article says
X resolves it" into "an engineer did X and it worked", and everything
downstream then treats it as observed: the playbook prompt tells the model
episode outcomes are empirical evidence a step works, patterns count them
as recurrence, and the agent cites them as [ep-N].

Found after a knowledge backfill took the corpus from 53 articles to 629 —
299 episodes had all-knowledge evidence, 8 from before the backfill, so the
gap predated it and was only too rare to notice.

Knowledge still correlates, embeds, reaches the graph and seeds patterns.
The gate is on episode synthesis alone.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from contextedge.workers.extraction_tasks import _cluster_has_observational_evidence


def _db(types, raises=False):
    """A session whose scalars().all() yields the cluster's evidence types."""
    if raises:
        return SimpleNamespace(execute=AsyncMock(side_effect=RuntimeError("db gone")))
    result = SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: list(types)))
    return SimpleNamespace(execute=AsyncMock(return_value=result))


@pytest.mark.asyncio
async def test_knowledge_only_cluster_is_refused():
    """The case this exists for."""
    assert (
        await _cluster_has_observational_evidence(
            _db(["kb_article"]), uuid4(), [uuid4(), uuid4()]
        )
        is False
    )


@pytest.mark.asyncio
async def test_ticket_cluster_is_allowed():
    assert (
        await _cluster_has_observational_evidence(
            _db(["ticket"]), uuid4(), [uuid4()]
        )
        is True
    )


@pytest.mark.asyncio
async def test_mixed_cluster_is_allowed():
    """A KB article cited alongside a real incident is supporting context,
    not a reason to refuse the incident its own episode."""
    assert (
        await _cluster_has_observational_evidence(
            _db(["kb_article", "ticket"]), uuid4(), [uuid4(), uuid4()]
        )
        is True
    )


@pytest.mark.asyncio
async def test_thread_messages_count_as_observational():
    """A conversation is a record of what people did and saw."""
    assert (
        await _cluster_has_observational_evidence(
            _db(["thread_message"]), uuid4(), [uuid4()]
        )
        is True
    )


@pytest.mark.asyncio
async def test_it_fails_open_on_a_database_error():
    """Wrongly allowing synthesis costs one reviewable draft. Wrongly
    blocking it means a real incident silently never becomes an episode."""
    assert (
        await _cluster_has_observational_evidence(
            _db([], raises=True), uuid4(), [uuid4()]
        )
        is True
    )


@pytest.mark.asyncio
async def test_it_fails_open_when_nothing_is_classifiable():
    assert (
        await _cluster_has_observational_evidence(_db([]), uuid4(), [uuid4()]) is True
    )
    assert await _cluster_has_observational_evidence(_db([]), uuid4(), []) is True


@pytest.mark.asyncio
async def test_non_string_rows_do_not_read_as_knowledge_only():
    """A stand-in session, or a row whose evidence_type is NULL, means we
    did not learn what the cluster is made of. That must read as allow —
    it briefly did not, and refused every cluster in the mocked tests."""
    assert (
        await _cluster_has_observational_evidence(
            _db([None, object()]), uuid4(), [uuid4()]
        )
        is True
    )


def test_the_gate_sits_immediately_before_synthesis():
    """Ordering is a cost decision. This gate costs a query, so every
    cheaper exit runs first — too small, locked, duplicate fingerprint —
    and only a cluster that would otherwise spend an LLM call pays for it.
    Two of those earlier exits are asserted elsewhere by exact
    db.execute counts, so moving this earlier breaks them."""
    import inspect

    from contextedge.workers import extraction_tasks

    source = inspect.getsource(extraction_tasks._reconstruct)
    gate_at = source.index("_cluster_has_observational_evidence")

    for cheaper_exit in (
        "skipped_below_min_cluster",
        "skipped_locked",
        "duplicate_cluster",
    ):
        assert source.index(cheaper_exit) < gate_at, cheaper_exit
