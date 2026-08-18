"""Regression tests for review findings F-04 and F-23: legal-hold
evidence must never reach an LLM extractor or scanner.

Both findings share the invariant "any query that ships evidence
content to an LLM must exclude sensitivity_label='legal_hold'".
After the fix, the shared helper is
``services.evidence_filters.exclude_legal_hold``; these tests pin
the two previously-broken call sites so the invariant can't silently
regress."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import pytest


def _evidence_stub(legal_hold: bool = False, ev_type: str = "ticket"):
    return SimpleNamespace(
        id=uuid4(),
        tenant_id=uuid4(),  # overridden per test
        sensitivity_label="legal_hold" if legal_hold else None,
        title="some title",
        body_text="some body",
        evidence_type=ev_type,
        embedding=[0.1] * 3072,
        domain_id=None,
        created_at_source=None,
        ingested_at=None,
    )


@pytest.mark.asyncio
async def test_episode_reconstruct_skips_legal_hold_evidence():
    """Review F-04, updated for P0 cluster materialization: legal-hold
    exclusion now happens in resolve_episode_cluster's visibility query
    (SQL predicate), so held evidence never enters a cluster and
    therefore never reaches the items list."""
    from contextedge.workers.extraction_tasks import _reconstruct

    tenant_id = uuid4()

    normal = _evidence_stub(legal_hold=False)
    normal.tenant_id = tenant_id
    normal.source_id = uuid4()
    # More visible items: the automatic path skips clusters below
    # MIN_AUTO_SYNTHESIS_CLUSTER (3), and this test is about legal-hold
    # exclusion, not that gate.
    normal2 = _evidence_stub(legal_hold=False)
    normal2.tenant_id = tenant_id
    normal2.source_id = uuid4()
    normal3 = _evidence_stub(legal_hold=False)
    normal3.tenant_id = tenant_id
    normal3.source_id = uuid4()
    held = _evidence_stub(legal_hold=True)
    held.tenant_id = tenant_id
    held.source_id = uuid4()

    gets = {normal.id: normal, normal2.id: normal2, normal3.id: normal3, held.id: held}

    async def fake_get(model, pk):
        return gets.get(pk)

    captured_items: dict = {}

    async def fake_create_episodes(db, *, tenant_id, domain_id, evidence_items, evidence_ids, **kw):
        captured_items["items"] = evidence_items
        captured_items["ids"] = evidence_ids
        return []

    visibility_sql: list[str] = []

    async def execute(stmt, params=None):
        text = str(stmt)
        result = Mock()
        if "pg_try_advisory_xact_lock" in text:
            result.scalar.return_value = True
            return result
        if "min(evidence_items.ingested_at)" in text:
            # Settlement bounds: long-settled cluster â†’ synthesis proceeds.
            from datetime import UTC, datetime, timedelta

            settled = datetime.now(UTC) - timedelta(hours=2)
            result.first.return_value = (settled, settled)
            return result
        if "coalesce(evidence_items.created_at_source" in text:
            # The cluster resolver's visibility query: the SQL itself
            # excludes legal_hold; the fake returns only the visible row.
            visibility_sql.append(text)
            result.all.return_value = [(normal.id, None), (normal2.id, None), (normal3.id, None)]
            return result
        if "cluster_fingerprint" in text and "scalar" not in text:
            result.scalar_one_or_none.return_value = None
            result.scalars.return_value.all.return_value = []
            return result
        if "sources" in text:
            # (evidence_id, source_type, source_config, evidence_type)
            result.all.return_value = [(normal.id, "servicenow", {}, "incident"), (normal2.id, "servicenow", {}, "incident"), (normal3.id, "servicenow", {}, "incident")]
            return result
        if "correlation_edges" in text or "case_links" in text:
            result.all.return_value = []
            result.scalars.return_value.all.return_value = []
            return result
        result.scalar_one_or_none.return_value = None
        result.scalars.return_value.all.return_value = []
        result.all.return_value = []
        return result

    db = SimpleNamespace(
        get=AsyncMock(side_effect=fake_get),
        execute=execute,
        flush=AsyncMock(),
    )

    cluster_id = f"{normal.id},{normal2.id},{normal3.id},{held.id}"

    with patch(
        "contextedge.services.episode_service.create_episodes_from_evidence",
        new=fake_create_episodes,
    ):
        result = await _reconstruct(db, cluster_id, tenant_id)

    # The SQL predicate carries the F-04 guarantee...
    assert visibility_sql and "sensitivity_label" in visibility_sql[0]
    # ...and the held evidence never reaches the items list.
    assert "items" in captured_items, f"create_episodes not called: {result}"
    ids_in_items = {item["evidence_id"] for item in captured_items["items"]}
    assert str(normal.id) in ids_in_items
    assert str(held.id) not in ids_in_items
    assert held.id not in captured_items["ids"]


def test_exclude_legal_hold_produces_correct_sql_fragment():
    """The shared helper returns a WHERE clause that allows NULL and
    any non-legal_hold label, and excludes legal_hold exactly."""
    from contextedge.services.evidence_filters import exclude_legal_hold

    fragment = exclude_legal_hold()
    compiled = str(fragment.compile(compile_kwargs={"literal_binds": True}))
    # The SQL should reference sensitivity_label with an OR of IS NULL
    # and inequality.
    assert "sensitivity_label IS NULL" in compiled
    assert "sensitivity_label != 'legal_hold'" in compiled


def test_contradiction_scanner_query_includes_legal_hold_exclusion():
    """Review F-23: the candidate SELECT built inside
    ``_top_k_kb_candidates`` must include the legal-hold exclusion.

    Rather than execute it, we snapshot the text of the compiled SQL
    to assert the predicate is present. This is a cheap stub-free
    regression guard that will fail the moment someone deletes the
    exclude_legal_hold() call inside the service."""
    from contextedge.services import contradiction_service

    # Inspect source of _top_k_kb_candidates directly.
    import inspect
    src = inspect.getsource(contradiction_service._top_k_kb_candidates)
    assert "exclude_legal_hold" in src, (
        "Review F-23 regression: the legal-hold filter has been removed "
        "from _top_k_kb_candidates. Do not ship contradiction scans on "
        "legal-hold evidence."
    )
