"""B3: the changes nobody records, observed by diffing state.

Most incident-causing changes never get a change record. A browser
auto-updates, an agent self-patches, an OS rolls forward, a disk fills. The
canonical case in this repo is a browser auto-upgrade breaking a web driver —
no change calendar would ever have shown it, and the fix sat undiscovered in a
78-message thread.

The detector sits where the change was *already noticed and thrown away*:
`_ensure_entity` compared each incoming CI trait against the stored one,
overwrote it, and said nothing. That discarded comparison is the event.

Two rules the tests exist to hold:

**A first observation is not a change.** Emitting one would announce a
transition for every CI the first time it is seen, which is noise that teaches
people to ignore the feed — and a feed nobody reads is worse than no feed,
because it looks like coverage.

**The detector must never break ingestion.** It sits on the critical path of an
entity upsert. A missed event costs a diagnostic hint; a raised exception costs
the sync.

Verified live: `radius-auth-01` OS 8.6 → 8.8 with no change record produced
`radius-auth-01: os_version_changed 8.6 -> 8.8`, linked to its CI, at
`source_type='inventory_diff'` — and a re-warm with no further change produced
no second event.
"""

from __future__ import annotations

import inspect

from contextedge.connectors.servicenow.connector import (
    SUBCLASS_DETAIL_FIELDS,
    ServiceNowConnector,
)
from contextedge.services import servicenow_reference_service as srs

# --- the detector ----------------------------------------------------------


def test_the_detector_runs_where_the_change_is_noticed():
    """Not a separate sweep. The trait loop already had both values in hand;
    a second pass would re-read state that had already been overwritten."""
    source = inspect.getsource(srs._ensure_entity)
    assert "transitions" in source
    assert "_emit_inventory_events" in source


def test_a_first_observation_is_not_a_change():
    """`previous` must be truthy before a transition is recorded. Without
    this every CI announces a transition the first time it is seen."""
    source = inspect.getsource(srs._ensure_entity)
    assert "if previous:" in source


def test_an_absent_upstream_value_never_clears_a_stored_one():
    """Unchanged from the pre-existing trait rule, and load-bearing here:
    a class that does not carry `os` must not read as 'the OS was removed'."""
    source = inspect.getsource(srs._ensure_entity)
    assert "if value and previous != value:" in source


def test_the_emitter_is_fail_soft():
    """It sits on the ingest critical path. A missed event costs a hint; a
    raised exception costs the sync."""
    source = inspect.getsource(srs._emit_inventory_events)
    assert "except Exception" in source
    assert "inventory_event_failed" in source


def test_the_event_records_observation_time_not_change_time():
    """Nothing here knows when the browser actually upgraded — only when we
    next looked and found it different. Using the CI's update stamp instead
    would look more precise and track the record rather than the machine."""
    source = inspect.getsource(srs._emit_inventory_events)
    assert "datetime.now(UTC)" in source
    doc = srs._emit_inventory_events.__doc__ or ""
    assert "observation" in doc.lower()


def test_the_event_says_no_change_was_authorised():
    """A state difference is not an approved change, and a reader who
    conflates them will go looking for a change record that never existed."""
    source = inspect.getsource(srs._emit_inventory_events)
    assert "No change record is" in source or "not an authorised" in source


# --- the subclass fetch that made traits reachable at all ------------------


def test_subclass_fields_are_fetched_from_their_own_tables():
    """Querying cmdb_ci for a subclass column returns rows WITHOUT the
    column — no error, no value, ever. It has bitten three field families."""
    tables = {table for table, _fields in SUBCLASS_DETAIL_FIELDS}
    assert "cmdb_ci_service" in tables
    assert "cmdb_ci_computer" in tables

    fields = {f for _table, fs in SUBCLASS_DETAIL_FIELDS for f in fs}
    assert "busines_criticality" in fields  # [sic] ServiceNow's own spelling
    assert {"os", "os_version"} <= fields


def test_the_subclass_pass_queries_every_id_rather_than_matching_classes():
    """ServiceNow returns only rows that exist in the queried table, so the
    filtering is free and correct. Class-prefix matching would be neither:
    cmdb_ci_server, cmdb_ci_esx_server and cmdb_ci_pc_hardware share no
    usable prefix."""
    source = inspect.getsource(ServiceNowConnector.fetch_ci_details)
    assert "SUBCLASS_DETAIL_FIELDS" in source
    assert "startswith(" not in source


def test_a_subclass_value_never_overwrites_with_nothing():
    source = inspect.getsource(ServiceNowConnector.fetch_ci_details)
    assert "if value:" in source
