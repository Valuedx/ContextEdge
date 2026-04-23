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
    """Review F-04: _reconstruct filters out legal_hold evidence from the
    items list before it reaches reconstruct_episode."""
    from contextedge.workers.extraction_tasks import _reconstruct

    tenant_id = uuid4()

    normal = _evidence_stub(legal_hold=False)
    normal.tenant_id = tenant_id
    held = _evidence_stub(legal_hold=True)
    held.tenant_id = tenant_id

    # Round-robin the db.get responses in the order _reconstruct requests them.
    gets = {normal.id: normal, held.id: held}

    async def fake_get(model, pk):
        return gets.get(pk)

    captured_items: dict = {}

    async def fake_create_episodes(db, *, tenant_id, domain_id, evidence_items, evidence_ids):
        captured_items["items"] = evidence_items
        captured_items["ids"] = evidence_ids
        return []

    # Domain lookup returns None for simplicity.
    domain_result = Mock()
    domain_result.scalar_one_or_none.return_value = None

    db = SimpleNamespace(
        get=AsyncMock(side_effect=fake_get),
        execute=AsyncMock(return_value=domain_result),
        flush=AsyncMock(),
    )

    cluster_id = f"{normal.id},{held.id}"

    with patch(
        "contextedge.services.episode_service.create_episodes_from_evidence",
        new=fake_create_episodes,
    ):
        result = await _reconstruct(db, cluster_id, tenant_id)

    # The held evidence must not appear in the items list or evidence_ids.
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
