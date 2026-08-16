"""What the source already states, recorded instead of re-inferred.

Two things a connector hands over and the pipeline used to ignore: whether
the case was resolved, and the labels a human put on it. Both were measured
on a live Zoho tenant before being built — 1000 resolved tickets, 84% of them
carrying a root cause from an eight-value taxonomy plus environment and
version — and both are refreshed on re-ingest, because a ticket's status and
its root cause are typically filled in AFTER the description stops changing.
"""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from contextedge.services.case_state import derive_case_state, is_resolved
from contextedge.services.source_facets import (
    applicability_from_facets,
    derive_facets,
)

_ZOHO_FACET_FIELDS = {
    "root_cause": "cf_rca",
    "environment": "cf_environment_information",
    "version": "cf_automation_egde_version_1",
    "component": "cf_enhancement",
    "customer": "cf_list_of_clients_1",
}


def _ticket(status="Closed", cf=None, **kw):
    return {
        "_connector_source_type": "zoho_desk",
        "_connector_object_type": "tickets",
        "status": status,
        "cf": cf if cf is not None else {},
        **kw,
    }


# =========================================================================
# Case state — what "over" means, and what it does not
# =========================================================================


@pytest.mark.parametrize(
    "status", ["Closed", "Resolved By Agent", "Resolved By Plugin Team", "resolved by network team"]
)
def test_the_resolved_vocabulary_is_read_from_the_field(status):
    """A tenant names its own resolution statuses. Matching Zoho's own
    `Resolved By ...` convention keeps "Resolved By Network Team" from being
    invisible, rather than needing a config entry per team."""
    assert derive_case_state(_ticket(status=status)) == "resolved"


def test_cancelled_is_terminal_but_not_resolved():
    """The case is over and there is no fix in it. Synthesising one spends
    exactly what the resolution gate exists to save."""
    assert derive_case_state(_ticket(status="Cancelled")) == "cancelled"
    assert is_resolved("cancelled") is False
    assert is_resolved("resolved") is True


@pytest.mark.parametrize("status", ["Open", "Work In Progress", "Awaiting Response - Customer"])
def test_a_running_ticket_asserts_nothing(status):
    assert derive_case_state(_ticket(status=status)) is None


def test_servicenow_numeric_states():
    for raw, expected in (("6", "resolved"), ("7", "resolved"), ("8", "cancelled"), ("2", None)):
        payload = {"_connector_source_type": "servicenow",
                   "_connector_object_type": "incident", "state": raw}
        assert derive_case_state(payload) == expected


def test_a_ticket_status_from_an_unmapped_source_is_ignored():
    """A `status` field exists on records from systems with no mapping, and
    it means something else there."""
    assert derive_case_state({"_connector_source_type": "gmail",
                              "_connector_object_type": "message",
                              "status": "Closed"}) is None


# =========================================================================
# Facets — recorded, never invented
# =========================================================================


def test_facets_come_from_the_sources_own_fields():
    payload = _ticket(cf={
        "cf_rca": "Access Permissions",
        "cf_environment_information": "T3",
        "cf_automation_egde_version_1": "8.2.3",
        "cf_enhancement": "AE Server",
    })
    facets = derive_facets(payload, _ZOHO_FACET_FIELDS)
    assert facets == {
        "root_cause": "Access Permissions",
        "environment": "T3",
        "version": "8.2.3",
        "component": "AE Server",
    }


def test_a_source_with_no_mapping_produces_nothing():
    """Facets are an opportunity where a system happens to be well-curated,
    not a requirement. Most sources will always return {}."""
    assert derive_facets(_ticket(cf={"cf_rca": "x"}), None) == {}
    assert derive_facets(_ticket(cf={"cf_rca": "x"}), {}) == {}


def test_unanswered_form_fields_are_not_facts():
    """"NA" is how a form records that nobody filled it in. Storing it would
    turn an unanswered question into a stated cause."""
    facets = derive_facets(
        _ticket(cf={"cf_rca": "NA", "cf_environment_information": "  ",
                    "cf_list_of_clients_1": "n/a"}),
        _ZOHO_FACET_FIELDS,
    )
    assert facets == {}


def test_an_unknown_facet_key_is_refused():
    """The keys are ours; the fields a deployment maps onto them are theirs.
    A typo must not create a facet nothing reads."""
    assert derive_facets(_ticket(cf={"cf_rca": "x"}), {"rootcause": "cf_rca"}) == {}


def test_top_level_custom_fields_are_found_too():
    """Zoho nests under `cf`; other connectors put them at the top level."""
    payload = _ticket(cf={}, incident_cause="Disk full")
    assert derive_facets(payload, {"root_cause": "incident_cause"}) == {"root_cause": "Disk full"}


# =========================================================================
# The saving: a stated value skips the model that would infer it
# =========================================================================


def test_stated_environment_and_version_become_applicability():
    """`knowledge_applicability` extracts exactly these from prose at ~7,200
    tokens a call. When the source states them, the statement wins."""
    out = applicability_from_facets(
        {"environment": "T3", "version": "8.2.3", "component": "AE Server"}
    )
    assert out["environments"] == ["T3"]
    assert out["versions"] == ["8.2.3"]
    assert out["source"] == "source_facets"


def test_no_facets_means_no_applicability_and_the_model_still_runs():
    """Absence must never look like an answer — an empty payload here leaves
    the extraction path exactly as it was."""
    assert applicability_from_facets({}) == {}
    assert applicability_from_facets(None) == {}


def test_a_root_cause_alone_does_not_fake_applicability():
    """Applicability is about where a procedure applies. A cause label is
    not that, and putting it there would make an article look scoped."""
    assert applicability_from_facets({"root_cause": "Access Permissions"}) == {}


# =========================================================================
# The gate reads the field before it reads the prose
# =========================================================================


@pytest.mark.asyncio
async def test_the_resolution_gate_trusts_the_source_verdict():
    """A ticket its own service desk marked resolved is resolved, whether or
    not anybody typed a resolution phrase into it."""
    from contextedge.services.resolution_signal_service import cluster_has_resolution_signal

    class _Db:
        def __init__(self):
            self.calls = 0

        async def execute(self, _stmt):
            self.calls += 1
            return SimpleNamespace(scalar=lambda: 1, all=lambda: [])

    db = _Db()
    assert await cluster_has_resolution_signal(db, uuid.uuid4(), [uuid.uuid4()]) is True
    # One query: it never had to scan any text.
    assert db.calls == 1


@pytest.mark.asyncio
async def test_no_resolved_evidence_falls_back_to_reading_the_text():
    from contextedge.services.resolution_signal_service import cluster_has_resolution_signal

    rows = [("VPN down", None, "resolved by re-issuing the certificate")]
    db = SimpleNamespace(execute=AsyncMock(side_effect=[
        SimpleNamespace(scalar=lambda: 0),
        SimpleNamespace(all=lambda: rows),
    ]))
    assert await cluster_has_resolution_signal(db, uuid.uuid4(), [uuid.uuid4()]) is True


@pytest.mark.asyncio
async def test_an_empty_cluster_never_queries():
    from contextedge.services.resolution_signal_service import cluster_has_resolution_signal

    db = SimpleNamespace(execute=AsyncMock())
    assert await cluster_has_resolution_signal(db, uuid.uuid4(), []) is False
    db.execute.assert_not_awaited()
