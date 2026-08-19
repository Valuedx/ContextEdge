"""Knowledge cases attach to patterns without becoming empirical evidence.

A case does not cluster with other cases — 600 articles behaving like 600
incidents is the failure the split exists to avoid. It attaches to the
pattern it documents, or seeds one when nothing covers it yet. That second
branch is the cold start: a pattern can exist before any incident does and
graduate as real ones arrive.
"""

from __future__ import annotations

import inspect
import uuid

import pytest

from contextedge.services import knowledge_case_service as svc


def test_attachment_is_stricter_than_clustering_match():
    """A wrong attachment is worse than a missed one: it puts a document
    behind a procedure it does not describe, and the playbook generator
    will cite it."""
    from contextedge.workers.pattern_tasks import PATTERN_MATCH_MAX_DISTANCE

    assert svc.KNOWLEDGE_ATTACH_MAX_DISTANCE < PATTERN_MATCH_MAX_DISTANCE


def test_documented_only_pattern_sits_below_the_playbook_floor():
    """A pattern supported only by documentation is a candidate, not
    something to write a procedure from until an incident confirms it."""
    from contextedge.workers.pattern_tasks import (
        PLAYBOOK_GENERATION_MIN_PATTERN_CONFIDENCE,
    )

    assert (
        svc.DOCUMENTED_ONLY_PATTERN_CONFIDENCE
        < PLAYBOOK_GENERATION_MIN_PATTERN_CONFIDENCE
    )


def test_ledger_rows_for_a_case_are_never_empirical():
    """Belt and braces over the CHECK constraint: the service must not
    even try. `documented` is hardcoded, and observed_at/outcome stay
    NULL because a document did not occur."""
    source = inspect.getsource(svc._record)

    assert 'evidence_class="documented"' in source
    assert "observed_at=None" in source
    assert "outcome=None" in source
    assert "empirical" not in source.replace("never `empirical`", "")


def test_nearest_pattern_query_is_ordered():
    """The bug clustering had: LIMIT 1 without ORDER BY returns an
    arbitrary qualifying pattern, and on a corpus this dense that is very
    nearly a random one."""
    source = inspect.getsource(svc._nearest_pattern)

    assert "ORDER BY distance ASC" in source
    assert "LIMIT 1" in source


def test_no_postgres_cast_operator_on_a_bound_parameter():
    """`:param::type` does not bind inside SQLAlchemy text() -- the `::`
    cast operator collides with `:param` syntax and the placeholder is
    passed through literally, which Postgres rejects at execution time.

    It cost 135 failed attachments to find, because nothing before
    execution can see it: the module imports, the SQL is a string, and
    `attach_case` catches the error and reports a status rather than
    raising. Use CAST(:param AS type)."""
    import re

    source = inspect.getsource(svc)

    assert not re.search(r":\w+::", source), (
        "bound parameter followed by a :: cast; use CAST(:param AS type)"
    )


def test_seeded_pattern_claims_no_episodes():
    """episode_count=0 is the honest number for a pattern nothing has
    happened to yet."""
    source = inspect.getsource(svc.attach_case)

    assert "episode_count=0" in source


@pytest.mark.asyncio
async def test_pattern_support_reports_documented_only_state():
    """The state a reviewer reads. documented_only is not a deficiency —
    it is a failure mode somebody wrote down before it happened here."""

    class _Result:
        def all(self):
            return [("documented", "supports_resolution", None, 2)]

    class _DB:
        async def execute(self, *_a, **_k):
            return _Result()

    support = await svc.pattern_support(_DB(), uuid.uuid4(), uuid.uuid4())

    assert support["documented"] == 2
    assert support["empirical"] == 0
    assert support["state"] == "documented_only"


@pytest.mark.asyncio
async def test_pattern_support_graduates_when_episodes_arrive():
    """The PATTERN graduates; the knowledge case does not."""

    class _Result:
        def all(self):
            return [
                ("documented", "supports_resolution", None, 1),
                ("empirical", "supports_resolution", "success", 14),
                ("empirical", "contradicts_resolution", "failure", 3),
            ]

    class _DB:
        async def execute(self, *_a, **_k):
            return _Result()

    support = await svc.pattern_support(_DB(), uuid.uuid4(), uuid.uuid4())

    assert support["state"] == "empirically_supported"
    assert support["documented"] == 1
    assert support["empirical_success"] == 14
    assert support["empirical_failure"] == 3
    # The count that makes stale-KB detection possible.
    assert support["contradicts"] == 3


@pytest.mark.asyncio
async def test_pattern_support_reports_unsupported_rather_than_guessing():
    class _Result:
        def all(self):
            return []

    class _DB:
        async def execute(self, *_a, **_k):
            return _Result()

    support = await svc.pattern_support(_DB(), uuid.uuid4(), uuid.uuid4())

    assert support["state"] == "unsupported"
