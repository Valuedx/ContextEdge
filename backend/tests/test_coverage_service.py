"""Coverage reports what is knowable, and each status implies a different move.

H2's whole value is in one discrimination: an empty result because nothing
happened, versus an empty result because nothing here can see. These tests pin
the seven-way decision, because collapsing any two of them turns a blind spot
back into a finding -- which is the failure the item exists to prevent.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from contextedge.services import coverage_service
from contextedge.services.coverage_service import (
    STALE_AFTER,
    CoverageReport,
    FacetCoverage,
    _facet_sync_state,
    _status_from,
    _SyncState,
)

NOW = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)


def _source(source_type: str = "servicenow"):
    return SimpleNamespace(source_type=source_type)


def _status(**kw) -> str:
    args = {
        "any_source": True,
        "capable": [_source()],
        "sync": _SyncState(discovered=True, selected=True, ever_synced=True),
        "count": 1,
        "now": NOW,
    }
    args.update(kw)
    return _status_from(**args)[0]


def test_no_sources_at_all_is_not_configured():
    assert _status(any_source=False, capable=[]) == "not_configured"


def test_connected_but_incapable_is_unsupported():
    """A Zoho-only deployment asked about changes. The honest answer is that
    the question cannot be asked, not that the answer is none."""
    assert _status(capable=[]) == "unsupported"


def test_capable_connector_but_instance_lacks_the_module_is_unavailable():
    """ServiceNow supports em_alert; this instance has no ITOM, so discovery
    never wrote a source object. Telling someone to approve it for sync sends
    them to a checkbox that does not exist."""
    assert _status(sync=_SyncState(discovered=False)) == "unavailable"


def test_discovered_but_unapproved_is_not_selected():
    assert _status(sync=_SyncState(discovered=True, selected=False)) == "not_selected"


def test_approved_but_never_synced_is_pending_not_empty():
    """Zero here means 'not fetched'. Reporting it as `empty` would license
    the conclusion that no records exist, from a sync that never ran."""
    assert (
        _status(
            sync=_SyncState(discovered=True, selected=True, ever_synced=False),
            count=0,
        )
        == "pending"
    )


def test_synced_with_nothing_found_is_empty():
    """The one status that is genuinely a finding."""
    assert _status(count=0) == "empty"


def test_old_successful_sync_is_stale_even_with_rows():
    old = NOW - STALE_AFTER - timedelta(days=1)
    assert (
        _status(
            sync=_SyncState(
                discovered=True, selected=True, ever_synced=True, last_sync=old
            ),
            count=5,
        )
        == "stale"
    )


def test_recent_sync_with_rows_is_available():
    fresh = NOW - timedelta(hours=1)
    assert (
        _status(
            sync=_SyncState(
                discovered=True, selected=True, ever_synced=True, last_sync=fresh
            ),
            count=5,
        )
        == "available"
    )


def test_unavailable_precedes_not_selected():
    """Order matters: an instance without the module is also unapproved, and
    reporting the second hides the first behind a fix that cannot work."""
    both = _SyncState(discovered=False, selected=False)
    assert _status(sync=both) == "unavailable"


def test_only_empty_stale_and_available_are_answerable():
    """`empty` is answerable -- the answer is "none". The other four are not,
    and an agent must not read them as zero."""
    answerable = {"available", "stale", "empty"}
    for status in (
        "available",
        "stale",
        "empty",
        "pending",
        "not_selected",
        "unavailable",
        "unsupported",
        "not_configured",
    ):
        facet = FacetCoverage(facet="changes", status=status)
        assert facet.is_answerable is (status in answerable), status


def test_blind_spots_lists_exactly_the_unanswerable_facets():
    report = CoverageReport(
        facets=(
            FacetCoverage(facet="changes", status="available", count=3),
            FacetCoverage(facet="monitoring", status="unavailable"),
            FacetCoverage(facet="topology", status="unsupported"),
            FacetCoverage(facet="problems", status="empty"),
        ),
        generated_at=NOW,
    )
    assert report.blind_spots == ("monitoring", "topology")
    assert report.by_facet("problems").is_answerable is True
    assert report.by_facet("nope") is None


def test_detail_names_the_capable_sources_so_the_message_is_actionable():
    """A status without the source name tells an operator something is wrong
    and not where to go."""
    _, detail = _status_from(
        any_source=True,
        capable=[_source("servicenow"), _source("jira_sm")],
        sync=_SyncState(discovered=True, selected=False),
        count=0,
        now=NOW,
    )
    assert "jira_sm" in detail and "servicenow" in detail


def test_report_serializes_for_the_api():
    report = CoverageReport(
        facets=(FacetCoverage(facet="changes", status="empty", sources=("servicenow",)),),
        generated_at=NOW,
    )
    payload = report.as_dict()
    assert payload["facets"][0]["answerable"] is True
    assert payload["facets"][0]["sources"] == ["servicenow"]
    assert payload["blind_spots"] == []
    assert payload["generated_at"].startswith("2026-08-21")


# --- connector shape ------------------------------------------------------
#
# Only ServiceNow names its source objects after its object types. Narrowing
# the sync lookup by object type is precise there and matches nothing on any
# other connector, which would report every facet on a Teams or Jira
# deployment as `unavailable` -- inventing a blind spot, the one error this
# module exists to prevent.


class _FakeSource:
    def __init__(self, source_type, ident=1):
        self.source_type = source_type
        self.id = ident


@pytest.mark.asyncio
async def test_object_type_narrowing_applies_when_the_connector_is_addressable(
    monkeypatch,
):
    """ServiceNow: discovery writes one SourceObject per table, so narrowing
    by object type is meaningful and `unavailable` is a real answer."""
    calls = []

    async def fake_any_source_object(db, tenant_id, source_ids, external_ids=None):
        calls.append(external_ids)
        if external_ids is None:
            return _SyncState(discovered=True, selected=True, ever_synced=True)
        # The vocabulary probe finds tables; the narrowed probe finds none,
        # which is exactly the em_alert case.
        if "incident" in (external_ids or []):
            return _SyncState(discovered=True, selected=True, ever_synced=True)
        return _SyncState(discovered=False)

    monkeypatch.setattr(
        coverage_service, "_any_source_object", fake_any_source_object
    )
    state = await _facet_sync_state(
        None, None, [_FakeSource("servicenow")], ["alert"]
    )
    assert state.discovered is False, "an absent table must stay absent"
    # Probed the vocabulary first, then narrowed -- never fell back to None.
    assert calls[0] is not None and calls[-1] is not None


@pytest.mark.asyncio
async def test_falls_back_to_source_level_when_not_addressable(monkeypatch):
    """Teams names objects `team:channel`, so no object-type narrowing can
    match. The facet must fall back, not report a blind spot that is really
    a naming mismatch."""
    calls = []

    async def fake_any_source_object(db, tenant_id, source_ids, external_ids=None):
        calls.append(external_ids)
        if external_ids is None:
            return _SyncState(discovered=True, selected=True, ever_synced=True)
        return _SyncState(discovered=False)  # vocabulary never matches

    monkeypatch.setattr(
        coverage_service, "_any_source_object", fake_any_source_object
    )
    state = await _facet_sync_state(
        None, None, [_FakeSource("teams")], ["chat_message"]
    )
    assert state.discovered is True
    assert state.selected is True
    assert calls[-1] is None, "must end on the unnarrowed source-level probe"


@pytest.mark.asyncio
async def test_no_capable_source_needs_no_query(monkeypatch):
    async def boom(*a, **k):  # pragma: no cover - must not be called
        raise AssertionError("should not query when nothing is capable")

    monkeypatch.setattr(coverage_service, "_any_source_object", boom)
    state = await _facet_sync_state(None, None, [], ["change"])
    assert state == _SyncState()
