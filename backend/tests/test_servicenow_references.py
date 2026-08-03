"""ServiceNow reference-field enrichment (Phase 1): deterministic case
links, typed graph edges, and CI / assignment-group entities."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import pytest

from contextedge.services.servicenow_reference_service import (
    EntityReference,
    _ensure_entity,
    _ref_sys_id,
    extract_entity_references,
    extract_task_references,
    heal_reverse_references,
    process_servicenow_references,
)

PROBLEM_SYS_ID = "a" * 32
CHANGE_SYS_ID = "b" * 32
CI_SYS_ID = "c" * 32
GROUP_SYS_ID = "d" * 32
OWN_SYS_ID = "e" * 32


class _NestedTx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _incident_payload(**overrides):
    payload = {
        "number": "INC0010427",
        "short_description": "Users cannot log in to the VPN",
        "problem_id": {"value": PROBLEM_SYS_ID, "link": "https://acme.service-now.com/x"},
        "caused_by": {"value": CHANGE_SYS_ID, "link": "https://acme.service-now.com/y"},
        "cmdb_ci": {"value": CI_SYS_ID, "link": "https://acme.service-now.com/z"},
        "cmdb_ci.name": "vpn-gw-east-01",
        "cmdb_ci.sys_class_name": "cmdb_ci_service",
        "assignment_group": {"value": GROUP_SYS_ID},
        "assignment_group.name": "Network Operations",
    }
    payload.update(overrides)
    return payload


# --- sys_id parsing ---------------------------------------------------------


def test_ref_sys_id_accepts_all_table_api_serializations():
    assert _ref_sys_id({"value": PROBLEM_SYS_ID, "link": "..."}) == PROBLEM_SYS_ID
    assert _ref_sys_id({"display_value": "PRB0004031", "value": PROBLEM_SYS_ID}) == PROBLEM_SYS_ID
    assert _ref_sys_id(PROBLEM_SYS_ID) == PROBLEM_SYS_ID
    assert _ref_sys_id(PROBLEM_SYS_ID.upper()) == PROBLEM_SYS_ID  # normalized


def test_ref_sys_id_rejects_display_strings_and_junk():
    """Display-value serializations must never become case-link keys."""
    assert _ref_sys_id("PRB0004031") is None
    assert _ref_sys_id("Network Operations") is None
    assert _ref_sys_id({"value": ""}) is None
    assert _ref_sys_id({}) is None
    assert _ref_sys_id(None) is None
    assert _ref_sys_id(42) is None


# --- extraction -------------------------------------------------------------


def test_task_references_map_fields_to_edge_types():
    refs = dict(extract_task_references(_incident_payload()))
    assert refs["related_problem"] == PROBLEM_SYS_ID
    assert refs["caused_by_change"] == CHANGE_SYS_ID


def test_task_references_exclude_shared_infrastructure():
    """cmdb_ci / assignment_group must not join the case-link namespace —
    hundreds of records share one CI; 1.0 links would mass-merge cases."""
    sys_ids = {sys_id for _, sys_id in extract_task_references(_incident_payload())}
    assert CI_SYS_ID not in sys_ids
    assert GROUP_SYS_ID not in sys_ids


def test_entity_references_use_dotwalked_names_and_class_mapping():
    ci, group = extract_entity_references(_incident_payload())
    assert ci.name == "vpn-gw-east-01"
    assert ci.entity_type == "business_service"  # cmdb_ci_service mapped
    assert ci.edge_type == "affects_ci"
    assert group.name == "Network Operations"
    assert group.entity_type == "assignment_group"


def test_entity_references_fall_back_when_dotwalks_missing():
    payload = {"cmdb_ci": {"value": CI_SYS_ID}}
    (ci,) = extract_entity_references(payload)
    assert ci.name == CI_SYS_ID
    assert ci.entity_type == "configuration_item"
    assert ci.attributes == {}


# --- case-link candidates ---------------------------------------------------


def test_case_link_candidates_include_task_reference_sys_ids():
    from contextedge.services.correlation_service import extract_case_link_candidates

    candidates = extract_case_link_candidates(
        source_type="servicenow",
        raw_object=SimpleNamespace(external_id=OWN_SYS_ID),
        raw_payload=_incident_payload(),
    )
    assert ("servicenow", OWN_SYS_ID) in candidates
    assert ("servicenow", PROBLEM_SYS_ID) in candidates
    assert ("servicenow", CHANGE_SYS_ID) in candidates
    assert ("servicenow", CI_SYS_ID) not in candidates
    assert ("servicenow", GROUP_SYS_ID) not in candidates


def test_case_link_candidates_unchanged_for_other_sources():
    from contextedge.services.correlation_service import extract_case_link_candidates

    candidates = extract_case_link_candidates(
        source_type="jira_sm",
        raw_object=SimpleNamespace(external_id="ISSUE-1"),
        raw_payload={"problem_id": {"value": PROBLEM_SYS_ID}},
    )
    assert ("jira_sm", PROBLEM_SYS_ID) not in candidates


# --- processing -------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_creates_typed_edges_and_entities(monkeypatch):
    from unittest.mock import AsyncMock as _AM
    monkeypatch.setattr(
        "contextedge.services.entity_class_service.ensure_entity_class_edges",
        _AM(return_value=None),
    )
    tenant_id = uuid4()
    evidence = SimpleNamespace(id=uuid4(), domain_id=None)
    problem_evidence_id = uuid4()

    async def resolve(db, tid, sys_id):
        return problem_evidence_id if sys_id == PROBLEM_SYS_ID else None

    from datetime import UTC, datetime

    entity = SimpleNamespace(id=uuid4(), last_synced_at=datetime.now(UTC))
    with (
        patch(
            "contextedge.services.servicenow_reference_service._resolve_evidence_for_sys_id",
            side_effect=resolve,
        ),
        patch(
            "contextedge.services.servicenow_reference_service._ensure_entity",
            AsyncMock(return_value=entity),
        ),
        patch(
            "contextedge.services.servicenow_reference_service.ensure_edge",
            AsyncMock(),
        ) as edge_mock,
    ):
        counts = await process_servicenow_references(
            SimpleNamespace(), tenant_id, evidence, _incident_payload()
        )

    assert counts["task_edges"] == 1  # problem resolved
    assert counts["unresolved_refs"] == 1  # change not yet ingested
    assert counts["entity_edges"] == 2  # CI + assignment group

    typed = [c.args for c in edge_mock.await_args_list if c.args[6] == "related_problem"]
    assert typed == [
        (
            edge_mock.await_args_list[0].args[0],
            tenant_id,
            "evidence",
            evidence.id,
            "evidence",
            problem_evidence_id,
            "related_problem",
        )
    ]


@pytest.mark.asyncio
async def test_process_skips_self_reference():
    tenant_id = uuid4()
    evidence = SimpleNamespace(id=uuid4(), domain_id=None)

    with (
        patch(
            "contextedge.services.servicenow_reference_service._resolve_evidence_for_sys_id",
            AsyncMock(return_value=evidence.id),
        ),
        patch(
            "contextedge.services.servicenow_reference_service.ensure_edge",
            AsyncMock(),
        ) as edge_mock,
    ):
        counts = await process_servicenow_references(
            SimpleNamespace(),
            tenant_id,
            evidence,
            {"problem_id": {"value": PROBLEM_SYS_ID}},
        )

    assert counts["task_edges"] == 0
    edge_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_reverse_heal_creates_edge_from_earlier_referencer():
    """Incident ingested before its problem: when the problem arrives, the
    incident's case-link row leads back to it and the typed edge points
    referencer → referenced."""
    tenant_id = uuid4()
    problem_evidence = SimpleNamespace(id=uuid4(), domain_id=None)
    incident_evidence = SimpleNamespace(
        id=uuid4(), tenant_id=tenant_id, raw_object_ref=uuid4(), domain_id=None
    )
    incident_raw = SimpleNamespace(id=incident_evidence.raw_object_ref)

    sibling_result = Mock()
    sibling_result.scalars.return_value.all.return_value = [incident_evidence.id]

    async def db_get(model, pk):
        if pk == incident_evidence.id:
            return incident_evidence
        if pk == incident_evidence.raw_object_ref:
            return incident_raw
        return None

    db = SimpleNamespace(
        execute=AsyncMock(return_value=sibling_result),
        get=AsyncMock(side_effect=db_get),
        begin_nested=Mock(return_value=_NestedTx()),
    )

    with (
        patch(
            "contextedge.services.artifact_extraction_service.load_raw_payload",
            AsyncMock(return_value={"problem_id": {"value": OWN_SYS_ID}}),
        ),
        patch(
            "contextedge.services.servicenow_reference_service.ensure_edge",
            AsyncMock(),
        ) as edge_mock,
    ):
        healed = await heal_reverse_references(
            db, tenant_id, problem_evidence, OWN_SYS_ID
        )

    assert healed == 1
    args = edge_mock.await_args_list[0].args
    assert args[2:7] == (
        "evidence",
        incident_evidence.id,
        "evidence",
        problem_evidence.id,
        "related_problem",
    )


@pytest.mark.asyncio
async def test_reverse_heal_ignores_siblings_not_referencing_us():
    """An earlier version of the SAME record shares our sys_id key but its
    payload references others, not us — no false edge."""
    tenant_id = uuid4()
    evidence = SimpleNamespace(id=uuid4(), domain_id=None)
    sibling = SimpleNamespace(
        id=uuid4(), tenant_id=tenant_id, raw_object_ref=uuid4(), domain_id=None
    )

    sibling_result = Mock()
    sibling_result.scalars.return_value.all.return_value = [sibling.id]

    async def db_get(model, pk):
        return sibling if pk == sibling.id else SimpleNamespace(id=pk)

    db = SimpleNamespace(
        execute=AsyncMock(return_value=sibling_result),
        get=AsyncMock(side_effect=db_get),
        begin_nested=Mock(return_value=_NestedTx()),
    )

    with (
        patch(
            "contextedge.services.artifact_extraction_service.load_raw_payload",
            AsyncMock(return_value={"problem_id": {"value": PROBLEM_SYS_ID}}),
        ),
        patch(
            "contextedge.services.servicenow_reference_service.ensure_edge",
            AsyncMock(),
        ) as edge_mock,
    ):
        healed = await heal_reverse_references(db, tenant_id, evidence, OWN_SYS_ID)

    assert healed == 0
    edge_mock.assert_not_awaited()


# --- entity upsert ----------------------------------------------------------


@pytest.mark.asyncio
async def test_ensure_entity_inserts_with_full_confidence():
    empty = Mock()
    empty.scalar_one_or_none.return_value = None
    added = []
    db = SimpleNamespace(
        execute=AsyncMock(return_value=empty),
        add=added.append,
        flush=AsyncMock(),
        begin_nested=Mock(return_value=_NestedTx()),
    )
    ref = EntityReference(
        sys_id=CI_SYS_ID,
        name="vpn-gw-east-01",
        entity_type="business_service",
        edge_type="affects_ci",
        attributes={"ci_class": "cmdb_ci_service"},
    )

    entity = await _ensure_entity(db, uuid4(), ref)

    assert added == [entity]
    assert entity.external_system == "servicenow"
    assert entity.external_id == CI_SYS_ID
    assert entity.name == "vpn-gw-east-01"
    assert entity.confidence == 1.0


@pytest.mark.asyncio
async def test_ensure_entity_refreshes_name_but_keeps_real_over_fallback():
    existing = SimpleNamespace(name="vpn-gw-east-01")
    found = Mock()
    found.scalar_one_or_none.return_value = existing
    db = SimpleNamespace(execute=AsyncMock(return_value=found))

    # sys_id fallback name must not clobber a real display name
    ref = EntityReference(
        sys_id=CI_SYS_ID, name=CI_SYS_ID, entity_type="configuration_item", edge_type="affects_ci"
    )
    out = await _ensure_entity(db, uuid4(), ref)
    assert out is existing
    assert existing.name == "vpn-gw-east-01"

    # a real rename does update
    ref = EntityReference(
        sys_id=CI_SYS_ID, name="vpn-gw-east-02", entity_type="configuration_item", edge_type="affects_ci"
    )
    await _ensure_entity(db, uuid4(), ref)
    assert existing.name == "vpn-gw-east-02"


# --- connector field lists --------------------------------------------------


def test_connector_requests_reference_and_dotwalk_fields():
    from contextedge.connectors.servicenow.connector import TABLES

    incident = TABLES["incident"]["fields"].split(",")
    for needed in (
        "problem_id", "rfc", "caused_by", "parent_incident",
        "cmdb_ci", "cmdb_ci.name", "cmdb_ci.sys_class_name",
        "assignment_group", "assignment_group.name", "close_code", "category",
    ):
        assert needed in incident
    assert "cmdb_ci" in TABLES["problem"]["fields"].split(",")
    assert "cmdb_ci" in TABLES["change_request"]["fields"].split(",")


@pytest.mark.asyncio
async def test_reverse_heal_survives_one_bad_sibling():
    """A failing sibling rolls back only its own savepoint; the loop
    continues and the good sibling still heals."""
    tenant_id = uuid4()
    evidence = SimpleNamespace(id=uuid4(), domain_id=None)
    bad = SimpleNamespace(
        id=uuid4(), tenant_id=tenant_id, raw_object_ref=uuid4(), domain_id=None
    )
    good = SimpleNamespace(
        id=uuid4(), tenant_id=tenant_id, raw_object_ref=uuid4(), domain_id=None
    )

    sibling_result = Mock()
    sibling_result.scalars.return_value.all.return_value = [bad.id, good.id]

    async def db_get(model, pk):
        for item in (bad, good):
            if pk == item.id:
                return item
            if pk == item.raw_object_ref:
                return SimpleNamespace(id=pk, owner=item)
        return None

    db = SimpleNamespace(
        execute=AsyncMock(return_value=sibling_result),
        get=AsyncMock(side_effect=db_get),
        begin_nested=Mock(return_value=_NestedTx()),
    )

    async def load_payload(raw):
        if raw.owner is bad:
            raise RuntimeError("object store unavailable")
        return {"problem_id": {"value": OWN_SYS_ID}}

    with (
        patch(
            "contextedge.services.artifact_extraction_service.load_raw_payload",
            AsyncMock(side_effect=load_payload),
        ),
        patch(
            "contextedge.services.servicenow_reference_service.ensure_edge",
            AsyncMock(),
        ) as edge_mock,
    ):
        healed = await heal_reverse_references(db, tenant_id, evidence, OWN_SYS_ID)

    assert healed == 1
    assert edge_mock.await_args_list[0].args[3] == good.id


# --- normalized traits (B2) -------------------------------------------------


def test_extract_ci_traits_only_present_values():
    from contextedge.services.servicenow_reference_service import extract_ci_traits

    payload = {
        "cmdb_ci.manufacturer.name": "Dell",
        "cmdb_ci.model_id.name": "Latitude 5420",
        "cmdb_ci.os": "",  # empty upstream -> absent, never guessed
    }
    assert extract_ci_traits(payload) == {
        "manufacturer": "Dell",
        "model": "Latitude 5420",
    }
    # Topology detail rows have no cmdb_ci. prefix.
    assert extract_ci_traits(
        {"os": "Windows", "os_version": "11 23H2"}, prefix=""
    ) == {"os_name": "Windows", "os_version": "11 23H2"}
    assert extract_ci_traits({}) == {}


def test_entity_reference_carries_traits():
    from contextedge.services.servicenow_reference_service import (
        extract_entity_references,
    )

    payload = {
        "cmdb_ci": {"value": "a" * 32},
        "cmdb_ci.name": "LPT001",
        "cmdb_ci.sys_class_name": "cmdb_ci_computer",
        "cmdb_ci.manufacturer.name": "Dell",
        "cmdb_ci.model_id.name": "Latitude 5420",
        "cmdb_ci.os": "Windows",
        "cmdb_ci.os_version": "11 23H2",
    }
    refs = extract_entity_references(payload)
    ci = next(r for r in refs if r.edge_type == "affects_ci")
    assert ci.traits == {
        "manufacturer": "Dell",
        "model": "Latitude 5420",
        "os_name": "Windows",
        "os_version": "11 23H2",
    }


@pytest.mark.asyncio
async def test_ensure_entity_writes_and_refreshes_traits():
    from contextedge.services.servicenow_reference_service import (
        EntityReference,
        _ensure_entity,
    )

    tenant_id = uuid4()
    added = []

    async def execute_missing(stmt):
        result = Mock()
        result.scalar_one_or_none.return_value = None
        return result

    class _Tx:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    db = SimpleNamespace(
        execute=execute_missing,
        add=added.append,
        flush=AsyncMock(),
        begin_nested=Mock(return_value=_Tx()),
    )
    ref = EntityReference(
        sys_id="a" * 32,
        name="LPT001",
        entity_type="configuration_item",
        edge_type="affects_ci",
        traits={"manufacturer": "Dell", "model": "Latitude 5420"},
    )
    created = await _ensure_entity(db, tenant_id, ref)
    assert created.manufacturer == "Dell"
    assert created.model == "Latitude 5420"
    assert created.os_name is None  # absent stays absent

    # Existing row: present values refresh, absent ones never clear.
    existing = SimpleNamespace(
        name="LPT001", manufacturer="Dell", model=None, os_name="Windows"
    )

    async def execute_existing(stmt):
        result = Mock()
        result.scalar_one_or_none.return_value = existing
        return result

    db2 = SimpleNamespace(execute=execute_existing)
    ref2 = EntityReference(
        sys_id="a" * 32,
        name="LPT001",
        entity_type="configuration_item",
        edge_type="affects_ci",
        traits={"model": "Latitude 5420"},  # os absent this sync
    )
    out = await _ensure_entity(db2, tenant_id, ref2)
    assert out is existing
    assert existing.model == "Latitude 5420"  # refreshed in place
    assert existing.os_name == "Windows"  # never cleared by absence
