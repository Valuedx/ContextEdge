"""C2: criticality, owning team and accountable owner on CI entities.

Blast radius without criticality is a list with no order, and a remediation
risk assessment on a Tier-1 service cannot be made without knowing it is
Tier-1. Three defects stood between the code and that, and each one looked
correct while returning nothing:

1. `_ensure_entity` wrote `attributes` only on INSERT. Criticality could
   therefore land only on a CI nobody had seen before — which, once a CMDB has
   been synced once, is none of them. A deployment that later populated those
   fields upstream would re-fetch them on every warm and store them never.

2. `busines_criticality` [sic, ServiceNow's own spelling] is defined on
   `cmdb_ci_service`, NOT on the `cmdb_ci` base table the neighborhood fetch
   queries. Asking the base table for it does not error; the column is simply
   absent from every row. Verified live: the same sys_id returns the field
   from /table/cmdb_ci_service and omits the key entirely from /table/cmdb_ci.

3. `owned_by` was captured nowhere at all.

These are source-inspection tests because the paths are DB- and network-bound,
following the convention in `test_applicability_on_ingest.py`. What matters is
that the calls and the merge are present on the path at all.
"""

from __future__ import annotations

import inspect

from contextedge.connectors.servicenow.connector import ServiceNowConnector
from contextedge.graph.agent import hydrators
from contextedge.services import cmdb_topology_service, servicenow_reference_service


def test_attributes_refresh_on_an_existing_entity():
    """The regression this module exists for. Without it C2 is write-once and
    every real CMDB is already past that write."""
    source = inspect.getsource(servicenow_reference_service._ensure_entity)

    assert "if ref.attributes:" in source
    assert "existing.attributes = merged" in source


def test_attributes_are_merged_not_replaced():
    """`attributes` is shared with other writers — ci_class from the class
    taxonomy, monitoring_sources from alert rollups. Assigning wholesale
    would drop whatever this caller did not happen to know."""
    source = inspect.getsource(servicenow_reference_service._ensure_entity)

    assert "merged = dict(existing.attributes or {})" in source
    # Key-by-key, and an absent upstream value never clears a stored one.
    assert "if value and merged.get(key) != value:" in source


def test_the_merged_dict_is_reassigned_rather_than_mutated():
    """SQLAlchemy does not track in-place mutation of a JSONB dict, so
    mutating it would update the object and never the row — a change that
    passes every assertion in memory and persists nothing."""
    source = inspect.getsource(servicenow_reference_service._ensure_entity)

    assert "existing.attributes = merged" in source
    assert "existing.attributes[" not in source


def test_criticality_is_fetched_from_the_service_table():
    """It does not exist on cmdb_ci. Requesting it there returns rows without
    the key and no error, which is why this looked wired for so long."""
    source = inspect.getsource(ServiceNowConnector.fetch_ci_details)

    assert "/api/now/table/cmdb_ci_service" in source
    assert "busines_criticality" in source


def test_the_service_lookup_is_skipped_when_no_service_is_present():
    """A pure-infrastructure neighborhood must still cost two calls."""
    source = inspect.getsource(ServiceNowConnector.fetch_ci_details)

    assert "if service_ids:" in source


def test_owner_and_support_group_are_both_carried():
    """A team says who is on call; an owner says who is accountable. Neither
    substitutes for the other, and escalating to a named individual who has
    left is worse than escalating to a queue."""
    connector_source = inspect.getsource(ServiceNowConnector.fetch_ci_details)
    assert "owned_by.name" in connector_source
    assert "support_group.name" in connector_source

    cache_source = inspect.getsource(cmdb_topology_service.cache_neighborhood)
    assert 'attributes["owner"]' in cache_source
    assert 'attributes["support_group"]' in cache_source


def test_absent_values_are_never_guessed():
    """An unset criticality stays unset. Defaulting it would make every CI
    look equally important, which is the same as none of them being."""
    cache_source = inspect.getsource(cmdb_topology_service.cache_neighborhood)

    assert "if criticality:" in cache_source
    assert "if owner:" in cache_source
    assert "if support_group:" in cache_source


def test_the_agent_projection_surfaces_owner():
    """Captured but unprojected is the same as uncaptured, from where the
    agent stands."""
    source = inspect.getsource(hydrators)

    marker = source.index('"criticality",')
    window = source[marker : marker + 400]
    assert '"owner",' in window
    assert '"support_group",' in window
