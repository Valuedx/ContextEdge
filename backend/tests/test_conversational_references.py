"""A4 conversational-reference resolver: triggers, candidate discipline,
abstention."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from contextedge.models.case_bridge import EvidenceCaseMembership
from contextedge.services.conversational_reference_service import (
    extract_reference_triggers,
    resolve_conversational_references,
)


class _NestedTx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


def _person(name):
    return SimpleNamespace(
        id=uuid4(), canonical_name=name, entity_type="person"
    )


def _device(name):
    return SimpleNamespace(
        id=uuid4(), canonical_name=name, entity_type="device"
    )


def _resolver_db(*, identities, person_cases=(), entity_ids=(), entity_cases=(), added=None):
    added = added if added is not None else []

    async def execute(stmt):
        text = str(stmt)
        result = Mock()
        if text.startswith("SELECT canonical_identities."):
            result.scalars.return_value.all.return_value = list(identities)
            return result
        if "evidence_identity_links" in text and "canonical_case_id" in text:
            result.scalars.return_value.all.return_value = list(person_cases)
            return result
        if text.startswith("SELECT entities.id"):
            result.scalars.return_value.all.return_value = list(entity_ids)
            return result
        if "graph_edges" in text:
            result.scalars.return_value.all.return_value = list(entity_cases)
            return result
        if text.startswith("SELECT evidence_case_memberships.id"):
            result.scalar_one_or_none.return_value = None
            return result
        result.scalar_one_or_none.return_value = None
        result.scalars.return_value.all.return_value = []
        return result

    return SimpleNamespace(
        execute=execute,
        add=added.append,
        flush=AsyncMock(),
        begin_nested=Mock(return_value=_NestedTx()),
    ), added


def _msg(body, function=None, thread_id=None):
    return SimpleNamespace(
        id=uuid4(),
        title=None,
        body_text=body,
        thread_id=thread_id,
        message_function=function,
        message_function_confidence=0.9 if function else None,
    )


def test_triggers_require_ticket_words():
    t = extract_reference_triggers("Can you look at John's ticket?")
    assert t["person_names"] == ["John"]
    t = extract_reference_triggers("any update on the ticket for the ordering server")
    assert t["entity_phrases"] == ["ordering server"]
    # A bare name or entity mention is NOT a trigger.
    assert extract_reference_triggers("John was in the meeting") == {
        "person_names": [],
        "entity_phrases": [],
    }
    assert extract_reference_triggers("the ordering server is slow") == {
        "person_names": [],
        "entity_phrases": [],
    }


@pytest.mark.asyncio
async def test_persons_single_active_case_resolves():
    tenant_id = uuid4()
    case = uuid4()
    john = _person("John Smith")
    db, added = _resolver_db(identities=[john], person_cases=[case])

    counts = await resolve_conversational_references(
        db, tenant_id, _msg("Can you look at John's ticket?")
    )

    assert counts["resolved"] == 1
    (m,) = [a for a in added if isinstance(a, EvidenceCaseMembership)]
    assert m.canonical_case_id == case
    assert m.extraction_location == "conversational_reference"


@pytest.mark.asyncio
async def test_person_with_multiple_active_cases_abstains():
    tenant_id = uuid4()
    john = _person("John Smith")
    db, added = _resolver_db(
        identities=[john], person_cases=[uuid4(), uuid4()]
    )

    counts = await resolve_conversational_references(
        db, tenant_id, _msg("please check John's ticket")
    )

    assert counts["resolved"] == 0
    assert counts["abstained"] == 1
    assert added == []


@pytest.mark.asyncio
async def test_unlinked_name_never_resolves():
    """The name must belong to an identity the extraction layer linked
    to THIS message — no free-text identity search."""
    tenant_id = uuid4()
    db, added = _resolver_db(identities=[])  # nothing linked

    counts = await resolve_conversational_references(
        db, tenant_id, _msg("check Maria's ticket please")
    )

    assert counts["resolved"] == 0
    assert added == []


@pytest.mark.asyncio
async def test_two_linked_matching_persons_abstain():
    tenant_id = uuid4()
    db, added = _resolver_db(
        identities=[_person("John Smith"), _person("John Doe")],
    )
    counts = await resolve_conversational_references(
        db, tenant_id, _msg("look at John's ticket")
    )
    assert counts["resolved"] == 0
    assert counts["abstained"] == 1
    assert added == []


@pytest.mark.asyncio
async def test_entity_path_resolves_through_affects_ci():
    tenant_id = uuid4()
    case = uuid4()
    server = _device("ordering server")
    db, added = _resolver_db(
        identities=[server], entity_ids=[uuid4()], entity_cases=[case]
    )

    counts = await resolve_conversational_references(
        db, tenant_id, _msg("any update on the ticket for the ordering server?")
    )

    assert counts["resolved"] == 1
    (m,) = [a for a in added if isinstance(a, EvidenceCaseMembership)]
    assert m.canonical_case_id == case


@pytest.mark.asyncio
async def test_conflicting_person_and_entity_resolutions_abstain():
    tenant_id = uuid4()
    john = _person("John Smith")
    server = _device("ordering server")
    db, added = _resolver_db(
        identities=[john, server],
        person_cases=[uuid4()],
        entity_ids=[uuid4()],
        entity_cases=[uuid4()],  # a DIFFERENT case than John's
    )

    counts = await resolve_conversational_references(
        db,
        tenant_id,
        _msg("is John's ticket the same as the ticket for the ordering server?"),
    )

    assert counts["resolved"] == 0
    assert counts["abstained"] >= 1
    assert added == []


@pytest.mark.asyncio
async def test_no_trigger_is_free():
    tenant_id = uuid4()
    calls = []

    async def execute(stmt):
        calls.append(1)
        raise AssertionError("no queries without a trigger")

    counts = await resolve_conversational_references(
        SimpleNamespace(execute=execute), tenant_id, _msg("thanks all, closing up")
    )
    assert counts == {"resolved": 0, "abstained": 0, "no_candidates": 0}
    assert calls == []


@pytest.mark.asyncio
async def test_dissociative_message_never_resolves():
    tenant_id = uuid4()
    john = _person("John Smith")
    db, added = _resolver_db(identities=[john], person_cases=[uuid4()])

    counts = await resolve_conversational_references(
        db,
        tenant_id,
        _msg("that is not John's ticket", function="dissociation"),
    )
    assert counts["resolved"] == 0
    assert added == []


@pytest.mark.asyncio
async def test_thread_negated_case_blocks_resolution():
    tenant_id = uuid4()
    case = uuid4()
    john = _person("John Smith")
    added = []

    async def execute(stmt):
        text = str(stmt)
        result = Mock()
        if text.startswith("SELECT canonical_identities."):
            result.scalars.return_value.all.return_value = [john]
            return result
        if "JOIN evidence_items" in text and "canonical_case_id" in text:
            result.scalars.return_value.all.return_value = [case]  # negated
            return result
        if "evidence_identity_links" in text and "canonical_case_id" in text:
            result.scalars.return_value.all.return_value = [case]
            return result
        result.scalar_one_or_none.return_value = None
        result.scalars.return_value.all.return_value = []
        return result

    db = SimpleNamespace(
        execute=execute,
        add=added.append,
        flush=AsyncMock(),
        begin_nested=Mock(return_value=_NestedTx()),
    )
    counts = await resolve_conversational_references(
        db, tenant_id, _msg("look at John's ticket", thread_id=uuid4())
    )
    assert counts["resolved"] == 0
    assert counts["abstained"] == 1
    assert added == []
