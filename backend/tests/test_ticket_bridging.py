"""P1 ticket-number bridging: membership, never case union."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from contextedge.services.ticket_bridge_service import (
    DIGEST_THRESHOLD,
    bridge_conversational_mentions,
    extract_ticket_tokens,
    register_ticket_identifier,
    ticket_display_number,
)


class _NestedTx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _scalar_one(value):
    result = Mock()
    result.scalar_one_or_none.return_value = value
    return result


def _scalars_result(values):
    result = Mock()
    result.scalars.return_value.all.return_value = values
    return result


# --- extraction -------------------------------------------------------------


def test_token_extraction_matches_ticket_shapes_only():
    text = (
        "Re: [INC0010427] VPN outage — also tracking ITOPS-101 and PRB0004031. "
        "Meeting at 14:30, order #12345 is unrelated, RITM0012345 raised."
    )
    tokens = extract_ticket_tokens(text)
    assert "INC0010427" in tokens
    assert "ITOPS-101" in tokens
    assert "PRB0004031" in tokens
    assert "RITM0012345" in tokens
    assert "12345" not in tokens  # bare numbers never match
    assert extract_ticket_tokens(None) == []


def test_ticket_display_number_per_source_shape():
    assert ticket_display_number("servicenow", {"number": "INC0010427"}) == "INC0010427"
    assert ticket_display_number("jira_sm", {"key": "itops-101"}) == "ITOPS-101"
    assert ticket_display_number("sapphireims", {"ticket_id": "INC-4021"}) == "INC-4021"
    assert ticket_display_number("teams", {"number": "X"}) is None
    assert ticket_display_number("servicenow", {}) is None


# --- registration + reconciliation ------------------------------------------


@pytest.mark.asyncio
async def test_ticket_registration_creates_identifier_membership_and_reconciles():
    tenant_id = uuid4()
    case_id = uuid4()
    ticket_evidence = SimpleNamespace(id=uuid4())
    waiting_email = uuid4()  # emailed about INC0010427 before the ticket arrived

    pending_mention = SimpleNamespace(
        evidence_id=waiting_email,
        extraction_location="subject",
        status="pending",
        resolved_case_id=None,
    )

    responses = [
        _scalar_one(None),                 # no existing identifier
        _scalar_one(None),                 # no existing primary membership
        _scalars_result([pending_mention]),  # one waiting mention
        _scalar_one(None),                 # no existing membership for the email
    ]
    added = []
    db = SimpleNamespace(
        execute=AsyncMock(side_effect=responses),
        add=added.append,
        flush=AsyncMock(),
        begin_nested=Mock(return_value=_NestedTx()),
    )

    counts = await register_ticket_identifier(
        db,
        tenant_id,
        evidence=ticket_evidence,
        source_type="servicenow",
        payload={"number": "INC0010427"},
        canonical_case_id=case_id,
    )

    assert counts == {
        "registered": True,
        "primary_membership": True,
        "reconciled_mentions": 1,
    }
    assert pending_mention.status == "resolved"
    assert pending_mention.resolved_case_id == case_id
    from contextedge.models.case_bridge import CaseIdentifier, EvidenceCaseMembership

    identifiers = [a for a in added if isinstance(a, CaseIdentifier)]
    memberships = [a for a in added if isinstance(a, EvidenceCaseMembership)]
    assert identifiers[0].normalized_value == "INC0010427"
    assert {m.relationship_type for m in memberships} == {
        "primary_case",
        "explicit_reference",
    }
    email_membership = next(
        m for m in memberships if m.relationship_type == "explicit_reference"
    )
    assert email_membership.evidence_id == waiting_email
    assert email_membership.confidence == 0.98  # subject-located mention


@pytest.mark.asyncio
async def test_identifier_case_mismatch_is_logged_never_clobbered():
    tenant_id = uuid4()
    existing = SimpleNamespace(canonical_case_id=uuid4())
    responses = [
        _scalar_one(existing),   # identifier already registered to another case
        _scalar_one(uuid4()),    # primary membership exists
        _scalars_result([]),     # no pending mentions
    ]
    db = SimpleNamespace(
        execute=AsyncMock(side_effect=responses),
        add=Mock(),
        flush=AsyncMock(),
        begin_nested=Mock(return_value=_NestedTx()),
    )

    counts = await register_ticket_identifier(
        db,
        tenant_id,
        evidence=SimpleNamespace(id=uuid4()),
        source_type="servicenow",
        payload={"number": "INC0010427"},
        canonical_case_id=uuid4(),  # different case
    )
    assert counts["registered"] is False
    assert existing.canonical_case_id != None  # noqa: E711 — unchanged


@pytest.mark.asyncio
async def test_registration_skips_without_case_or_number():
    db = SimpleNamespace()
    no_case = await register_ticket_identifier(
        db, uuid4(), evidence=SimpleNamespace(id=uuid4()),
        source_type="servicenow", payload={"number": "INC1"}, canonical_case_id=None,
    )
    assert no_case["registered"] is False
    no_number = await register_ticket_identifier(
        db, uuid4(), evidence=SimpleNamespace(id=uuid4()),
        source_type="servicenow", payload={}, canonical_case_id=uuid4(),
    )
    assert no_number["registered"] is False


# --- conversational bridging ------------------------------------------------


def _conversational_db(resolution_map, membership_exists=False):
    """resolution_map: token -> case_id or None."""
    state = {"added": []}

    async def execute(stmt):
        text = str(stmt)
        result = Mock()
        if "case_identifiers" in text:
            params = stmt.compile().params
            token = next(
                (v for v in params.values() if isinstance(v, str) and v in resolution_map),
                None,
            )
            case_id = resolution_map.get(token)
            # Ambiguity-aware lookup consumes scalars().all().
            result.scalars.return_value.all.return_value = (
                [case_id] if case_id is not None else []
            )
            return result
        # membership / pending existence checks
        result.scalar_one_or_none.return_value = uuid4() if membership_exists else None
        return result

    db = SimpleNamespace(
        execute=execute,
        add=state["added"].append,
        flush=AsyncMock(),
        begin_nested=Mock(return_value=_NestedTx()),
    )
    return db, state


@pytest.mark.asyncio
async def test_email_quoting_known_ticket_gets_membership():
    tenant_id = uuid4()
    case_id = uuid4()
    email = SimpleNamespace(
        id=uuid4(),
        title="Re: [INC0010427] VPN outage",
        body_text="Users still cannot log in.",
    )
    db, state = _conversational_db({"INC0010427": case_id})

    counts = await bridge_conversational_mentions(db, tenant_id, email)

    assert counts["memberships"] == 1
    assert counts["digest_downgraded"] is False
    from contextedge.models.case_bridge import EvidenceCaseMembership

    (membership,) = [
        a for a in state["added"] if isinstance(a, EvidenceCaseMembership)
    ]
    assert membership.canonical_case_id == case_id
    assert membership.relationship_type == "explicit_reference"
    assert membership.confidence == 0.98
    assert membership.extraction_location == "subject"


@pytest.mark.asyncio
async def test_multi_ticket_digest_downgrades_to_mentioned_only():
    """'Weekly review of INC..., INC..., CHG...' must relate, never merge."""
    tenant_id = uuid4()
    cases = {f"INC001042{i}": uuid4() for i in range(DIGEST_THRESHOLD)}
    digest = SimpleNamespace(
        id=uuid4(),
        title="Weekly incident review",
        body_text="Covering " + ", ".join(cases) + " this week.",
    )
    db, state = _conversational_db(cases)

    counts = await bridge_conversational_mentions(db, tenant_id, digest)

    assert counts["digest_downgraded"] is True
    from contextedge.models.case_bridge import EvidenceCaseMembership

    memberships = [a for a in state["added"] if isinstance(a, EvidenceCaseMembership)]
    assert len(memberships) == DIGEST_THRESHOLD
    assert all(m.relationship_type == "mentioned_only" for m in memberships)
    assert all(m.confidence == 0.5 for m in memberships)


@pytest.mark.asyncio
async def test_unknown_tokens_become_pending_mentions():
    tenant_id = uuid4()
    email = SimpleNamespace(
        id=uuid4(),
        title="INC0999999 filed for the VPN outage",
        body_text="",
    )
    db, state = _conversational_db({})  # nothing registered yet

    counts = await bridge_conversational_mentions(db, tenant_id, email)

    assert counts["memberships"] == 0
    assert counts["pending"] == 1
    from contextedge.models.case_bridge import PendingIdentifierMention

    (mention,) = [
        a for a in state["added"] if isinstance(a, PendingIdentifierMention)
    ]
    assert mention.normalized_value == "INC0999999"
    assert mention.extraction_location == "subject"


# --- cluster expansion ------------------------------------------------------


@pytest.mark.asyncio
async def test_cluster_expands_via_memberships_but_not_mentioned_only():
    from contextedge.services.episode_cluster_service import resolve_episode_cluster

    tenant_id = uuid4()
    ticket = uuid4()
    email = uuid4()
    case_id = uuid4()
    from datetime import UTC, datetime

    now = datetime(2026, 8, 1, 12, 0, 0, tzinfo=UTC)
    call = {"n": 0}

    async def execute(stmt):
        call["n"] += 1
        text = str(stmt)
        result = Mock()
        if "evidence_items" in text:
            if call["n"] == 1:
                result.all.return_value = [(ticket, now)]
            else:
                result.all.return_value = [(email, now)]
            return result
        if text.startswith("SELECT evidence_case_memberships.canonical_case_id"):
            result.scalars.return_value.all.return_value = [case_id]
            return result
        membership_select = (
            "SELECT evidence_case_memberships.evidence_id, "
            "evidence_case_memberships.relationship_type"
        )
        if text.startswith(membership_select):
            # The digest email is filtered OUT by the SQL predicate in
            # production; the fake returns only the explicit reference.
            assert "relationship_type !=" in text  # mentioned_only excluded
            result.all.return_value = [(email, "explicit_reference")]
            return result
        result.scalars.return_value.all.return_value = []
        result.all.return_value = []
        return result

    cluster = await resolve_episode_cluster(
        SimpleNamespace(execute=execute), tenant_id, [ticket]
    )
    assert set(cluster.evidence_ids) == {ticket, email}
    assert "ticket_ref:explicit_reference" in cluster.reasons[str(email)]


@pytest.mark.asyncio
async def test_ambiguous_identifier_abstains():
    """The same value registered by two systems (SapphireIMS INC-4021
    also matches the Jira key shape) must abstain, never pick arbitrarily."""
    tenant_id = uuid4()
    email = SimpleNamespace(id=uuid4(), title="About INC-4021", body_text="")
    state = {"added": []}

    async def execute(stmt):
        result = Mock()
        if "case_identifiers" in str(stmt):
            result.scalars.return_value.all.return_value = [uuid4(), uuid4()]
            return result
        result.scalar_one_or_none.return_value = None
        return result

    db = SimpleNamespace(
        execute=execute,
        add=state["added"].append,
        flush=AsyncMock(),
        begin_nested=Mock(return_value=_NestedTx()),
    )

    counts = await bridge_conversational_mentions(db, tenant_id, email)
    assert counts["memberships"] == 0
    assert counts.get("ambiguous") == 1
    assert counts["pending"] == 0  # ambiguous is not unknown
    assert state["added"] == []


# --- reply inheritance ------------------------------------------------------


def _reply_db(parent_evidence_id, parent_cases, added):
    async def execute(stmt):
        text = str(stmt)
        result = Mock()
        if "sources" in text and "raw_evidence_objects" in text:
            result.scalar_one_or_none.return_value = parent_evidence_id
            return result
        if text.startswith("SELECT evidence_case_memberships.canonical_case_id"):
            result.scalars.return_value.all.return_value = parent_cases
            return result
        result.scalar_one_or_none.return_value = None
        return result

    return SimpleNamespace(
        execute=execute,
        add=added.append,
        flush=AsyncMock(),
        begin_nested=Mock(return_value=_NestedTx()),
    )


@pytest.mark.asyncio
async def test_reply_inherits_single_case_parent():
    from contextedge.services.ticket_bridge_service import inherit_reply_membership

    tenant_id = uuid4()
    case_id = uuid4()
    added = []
    reply = SimpleNamespace(id=uuid4(), title=None, body_text="Has it recovered now?")
    db = _reply_db(uuid4(), [case_id], added)

    counts = await inherit_reply_membership(
        db, tenant_id, reply, {"reply_to_id": "root-msg-1"}
    )

    assert counts["inherited"] == 1
    from contextedge.models.case_bridge import EvidenceCaseMembership

    (membership,) = [a for a in added if isinstance(a, EvidenceCaseMembership)]
    assert membership.canonical_case_id == case_id
    assert membership.relationship_type == "reply_inheritance"
    assert membership.extraction_location == "reply_structure"


@pytest.mark.asyncio
async def test_dissociation_language_vetoes_inheritance():
    from contextedge.services.ticket_bridge_service import inherit_reply_membership

    tenant_id = uuid4()
    added = []
    reply = SimpleNamespace(
        id=uuid4(),
        title=None,
        body_text="Different issue, but is the ordering database also down?",
    )
    db = _reply_db(uuid4(), [uuid4()], added)

    counts = await inherit_reply_membership(
        db, tenant_id, reply, {"reply_to_id": "root-msg-1"}
    )

    assert counts["vetoed"] is True
    assert counts["inherited"] == 0
    assert added == []


@pytest.mark.asyncio
async def test_multi_case_parent_abstains():
    from contextedge.services.ticket_bridge_service import inherit_reply_membership

    tenant_id = uuid4()
    added = []
    reply = SimpleNamespace(id=uuid4(), title=None, body_text="Any update?")
    db = _reply_db(uuid4(), [uuid4(), uuid4()], added)

    counts = await inherit_reply_membership(
        db, tenant_id, reply, {"reply_to_id": "root-msg-1"}
    )

    assert counts["abstained"] is True
    assert counts["inherited"] == 0


@pytest.mark.asyncio
async def test_no_reply_target_or_unknown_parent_is_noop():
    from contextedge.services.ticket_bridge_service import inherit_reply_membership

    tenant_id = uuid4()
    reply = SimpleNamespace(id=uuid4(), title=None, body_text="hello")
    no_target = await inherit_reply_membership(
        SimpleNamespace(), tenant_id, reply, {}
    )
    assert no_target == {"inherited": 0, "vetoed": False, "abstained": False}

    added = []
    db = _reply_db(None, [], added)  # parent message never ingested
    unknown = await inherit_reply_membership(
        db, tenant_id, reply, {"reply_to_id": "gone"}
    )
    assert unknown["inherited"] == 0
    assert added == []


# --- correction supersession (A2) ------------------------------------------


def _correction_db(parent_evidence_id, target_memberships, own_cases, added, events):
    async def execute(stmt):
        text = str(stmt)
        result = Mock()
        if "sources" in text and "raw_evidence_objects" in text:
            result.scalar_one_or_none.return_value = parent_evidence_id
            return result
        if text.startswith("SELECT evidence_case_memberships.canonical_case_id"):
            result.scalars.return_value.all.return_value = own_cases
            return result
        if text.startswith("SELECT evidence_case_memberships.id,"):
            # Whole-entity select (id, tenant_id, ...) = the retire query.
            result.scalars.return_value.all.return_value = target_memberships
            return result
        if text.startswith("SELECT evidence_case_memberships.id"):
            result.scalar_one_or_none.return_value = None  # _add_membership check
            return result
        if text.startswith("SELECT evidence_items.id"):
            result.scalar_one_or_none.return_value = None
            return result
        result.scalar_one_or_none.return_value = None
        return result

    return SimpleNamespace(
        execute=execute,
        add=added.append,
        flush=AsyncMock(),
        begin_nested=Mock(return_value=_NestedTx()),
    )


def _correction_evidence(confidence=0.9, thread_id=None):
    return SimpleNamespace(
        id=uuid4(),
        thread_id=thread_id,
        message_function="correction",
        message_function_confidence=confidence,
        title=None,
        body_text="Correction - tracking under INC0010455",
    )


@pytest.mark.asyncio
async def test_correction_supersedes_and_propagates(monkeypatch):
    from contextedge.services import ticket_bridge_service as tbs

    tenant_id = uuid4()
    parent_id = uuid4()
    old_case, new_case = uuid4(), uuid4()
    wrong = SimpleNamespace(
        canonical_case_id=old_case,
        relationship_type="reply_inheritance",
        status="active",
    )
    added, events = [], []

    async def fake_event(db, **kwargs):
        events.append(kwargs)

    monkeypatch.setattr(
        "contextedge.services.event_log_service.append_operational_event", fake_event
    )
    db = _correction_db(parent_id, [wrong], [new_case], added, events)

    counts = await tbs.apply_correction(
        db, tenant_id, _correction_evidence(), {"reply_to_id": "msg-1"}
    )

    assert counts["superseded"] == 1
    assert counts["propagated"] == 1
    assert counts["target_found"] is True
    assert counts["corrected_case_id"] == str(new_case)
    assert wrong.status == "corrected"
    from contextedge.models.case_bridge import EvidenceCaseMembership

    (new_row,) = [a for a in added if isinstance(a, EvidenceCaseMembership)]
    assert new_row.canonical_case_id == new_case
    assert new_row.evidence_id == parent_id
    assert new_row.extraction_location == "correction"
    assert events and events[0]["event_type"] == "correlation.correction_applied"
    assert events[0]["payload"]["superseded_case_ids"] == [str(old_case)]


@pytest.mark.asyncio
async def test_vague_correction_supersedes_without_relinking(monkeypatch):
    from contextedge.services import ticket_bridge_service as tbs

    tenant_id = uuid4()
    wrong = SimpleNamespace(
        canonical_case_id=uuid4(),
        relationship_type="explicit_reference",
        status="active",
    )
    added, events = [], []

    async def fake_event(db, **kwargs):
        events.append(kwargs)

    monkeypatch.setattr(
        "contextedge.services.event_log_service.append_operational_event", fake_event
    )
    db = _correction_db(uuid4(), [wrong], [], added, events)

    counts = await tbs.apply_correction(
        db, tenant_id, _correction_evidence(), {"reply_to_id": "msg-1"}
    )

    assert counts["superseded"] == 1
    assert counts["propagated"] == 0
    assert wrong.status == "corrected"
    assert added == []
    assert events[0]["payload"]["corrected_case_id"] is None


@pytest.mark.asyncio
async def test_ambiguous_correction_never_relinks(monkeypatch):
    from contextedge.services import ticket_bridge_service as tbs

    tenant_id = uuid4()
    added, events = [], []

    async def fake_event(db, **kwargs):
        events.append(kwargs)

    monkeypatch.setattr(
        "contextedge.services.event_log_service.append_operational_event", fake_event
    )
    db = _correction_db(uuid4(), [], [uuid4(), uuid4()], added, events)

    counts = await tbs.apply_correction(
        db, tenant_id, _correction_evidence(), {"reply_to_id": "msg-1"}
    )

    assert counts["propagated"] == 0
    assert added == []


@pytest.mark.asyncio
async def test_low_confidence_or_wrong_label_is_noop():
    from contextedge.services import ticket_bridge_service as tbs

    tenant_id = uuid4()
    low = _correction_evidence(confidence=0.3)
    counts = await tbs.apply_correction(
        SimpleNamespace(), tenant_id, low, {"reply_to_id": "msg-1"}
    )
    assert counts == {"superseded": 0, "propagated": 0, "target_found": False}

    status = _correction_evidence()
    status.message_function = "status_update"
    counts = await tbs.apply_correction(
        SimpleNamespace(), tenant_id, status, {"reply_to_id": "msg-1"}
    )
    assert counts["target_found"] is False


@pytest.mark.asyncio
async def test_primary_case_membership_is_never_corrected(monkeypatch):
    """The retire query filters to conversational relationship types; a
    ticket's own primary_case row is untouchable from chat. Verified by
    asserting the SQL the service issues carries the IN filter."""
    from contextedge.services import ticket_bridge_service as tbs

    tenant_id = uuid4()
    seen_sql = []

    async def execute(stmt):
        text = str(stmt)
        seen_sql.append(text)
        result = Mock()
        if "sources" in text and "raw_evidence_objects" in text:
            result.scalar_one_or_none.return_value = uuid4()
            return result
        result.scalars.return_value.all.return_value = []
        result.scalar_one_or_none.return_value = None
        return result

    db = SimpleNamespace(
        execute=execute,
        add=lambda o: None,
        flush=AsyncMock(),
        begin_nested=Mock(return_value=_NestedTx()),
    )
    await tbs.apply_correction(
        db, tenant_id, _correction_evidence(), {"reply_to_id": "msg-1"}
    )

    retire_sql = [
        s
        for s in seen_sql
        if s.startswith("SELECT evidence_case_memberships.")
        and "relationship_type IN" in s
    ]
    assert retire_sql, "retire query must filter relationship_type IN correctable set"


# --- negative evidence store (A7) -------------------------------------------


def _negation_db(resolved_case, thread_negated, added):
    """Fake for the bridge with one resolvable token; routes the
    thread-negation join and _add_membership existence check."""

    async def execute(stmt):
        text = str(stmt)
        result = Mock()
        if text.startswith("SELECT case_identifiers.canonical_case_id"):
            result.scalars.return_value.all.return_value = (
                [resolved_case] if resolved_case else []
            )
            return result
        if "JOIN evidence_items" in text and "canonical_case_id" in text:
            result.scalars.return_value.all.return_value = thread_negated
            return result
        if text.startswith("SELECT evidence_case_memberships.id"):
            result.scalar_one_or_none.return_value = None
            return result
        if text.startswith("SELECT pending_identifier_mentions.id"):
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
    )


def _teams_evidence(body, function=None, confidence=None, thread_id=None):
    return SimpleNamespace(
        id=uuid4(),
        title=None,
        body_text=body,
        thread_id=thread_id,
        message_function=function,
        message_function_confidence=confidence,
    )


@pytest.mark.asyncio
async def test_dissociative_mention_becomes_negative_row():
    """"Not related to INC0010427" is evidence AGAINST the link."""
    from contextedge.services.ticket_bridge_service import (
        bridge_conversational_mentions,
    )

    tenant_id = uuid4()
    case_id = uuid4()
    added = []
    ev = _teams_evidence(
        "this is not related to INC0010427",
        function="dissociation",
        confidence=0.9,
    )
    db = _negation_db(case_id, [], added)

    counts = await bridge_conversational_mentions(db, tenant_id, ev)

    assert counts.get("negated") == 1
    assert counts["memberships"] == 0
    from contextedge.models.case_bridge import EvidenceCaseMembership

    (row,) = [a for a in added if isinstance(a, EvidenceCaseMembership)]
    assert row.status == "negative"
    assert row.relationship_type == "dissociation"


@pytest.mark.asyncio
async def test_dissociative_unknown_token_becomes_tagged_pending():
    from contextedge.services.ticket_bridge_service import (
        bridge_conversational_mentions,
    )

    tenant_id = uuid4()
    added = []
    ev = _teams_evidence(
        "not related to INC0099999", function="dissociation", confidence=0.9
    )
    db = _negation_db(None, [], added)

    counts = await bridge_conversational_mentions(db, tenant_id, ev)

    assert counts["pending"] == 1
    from contextedge.models.case_bridge import PendingIdentifierMention

    (mention,) = [a for a in added if isinstance(a, PendingIdentifierMention)]
    assert mention.extraction_location == "dissociation"


@pytest.mark.asyncio
async def test_thread_negation_blocks_later_plain_mention():
    """The acceptance case: after a dissociation, a later plain mention
    in the SAME thread must not re-create an active membership."""
    from contextedge.services.ticket_bridge_service import (
        bridge_conversational_mentions,
    )

    tenant_id = uuid4()
    case_id = uuid4()
    thread = uuid4()
    added = []
    later = _teams_evidence("update on INC0010427 please", thread_id=thread)
    db = _negation_db(case_id, [case_id], added)

    counts = await bridge_conversational_mentions(db, tenant_id, later)

    assert counts.get("blocked_by_negative") == 1
    assert counts["memberships"] == 0
    assert added == []


@pytest.mark.asyncio
async def test_thread_negation_blocks_reply_inheritance():
    from contextedge.services.ticket_bridge_service import inherit_reply_membership

    tenant_id = uuid4()
    case_id = uuid4()
    added = []
    reply = SimpleNamespace(
        id=uuid4(),
        title=None,
        body_text="thanks, checking",
        thread_id=uuid4(),
        message_function=None,
        message_function_confidence=None,
    )

    async def execute(stmt):
        text = str(stmt)
        result = Mock()
        if "sources" in text and "raw_evidence_objects" in text:
            result.scalar_one_or_none.return_value = uuid4()
            return result
        if "JOIN evidence_items" in text and "canonical_case_id" in text:
            result.scalars.return_value.all.return_value = [case_id]
            return result
        if text.startswith("SELECT evidence_case_memberships.canonical_case_id"):
            result.scalars.return_value.all.return_value = [case_id]
            return result
        result.scalar_one_or_none.return_value = None
        return result

    db = SimpleNamespace(
        execute=execute,
        add=added.append,
        flush=AsyncMock(),
        begin_nested=Mock(return_value=_NestedTx()),
    )

    counts = await inherit_reply_membership(
        db, tenant_id, reply, {"reply_to_id": "root-1"}
    )

    assert counts.get("blocked_by_negative") is True
    assert counts["inherited"] == 0
    assert added == []


@pytest.mark.asyncio
async def test_reconciliation_of_dissociation_mention_writes_negative():
    from contextedge.services.ticket_bridge_service import register_ticket_identifier

    tenant_id = uuid4()
    case_id = uuid4()
    mention = SimpleNamespace(
        evidence_id=uuid4(),
        extraction_location="dissociation",
        status="pending",
        resolved_case_id=None,
    )
    added = []

    async def execute(stmt):
        text = str(stmt)
        result = Mock()
        if text.startswith("SELECT case_identifiers.id,"):
            result.scalar_one_or_none.return_value = None
            return result
        if text.startswith("SELECT pending_identifier_mentions.id,"):
            result.scalars.return_value.all.return_value = [mention]
            return result
        if text.startswith("SELECT evidence_case_memberships.id"):
            result.scalar_one_or_none.return_value = None
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

    counts = await register_ticket_identifier(
        db,
        tenant_id,
        evidence=SimpleNamespace(id=uuid4()),
        source_type="servicenow",
        payload={"number": "INC0010427"},
        canonical_case_id=case_id,
    )

    assert counts["reconciled_mentions"] == 1
    assert mention.status == "resolved"
    from contextedge.models.case_bridge import EvidenceCaseMembership

    negative_rows = [
        a
        for a in added
        if isinstance(a, EvidenceCaseMembership) and a.status == "negative"
    ]
    assert len(negative_rows) == 1
    assert negative_rows[0].evidence_id == mention.evidence_id


@pytest.mark.asyncio
async def test_confident_correction_mention_stays_positive():
    """A2/A7 interplay: the correction ABSTAINS from inheritance but its
    own ticket mention is a positive link A2 propagates from."""
    from contextedge.services.ticket_bridge_service import (
        bridge_conversational_mentions,
    )

    tenant_id = uuid4()
    case_id = uuid4()
    added = []
    ev = _teams_evidence(
        "Correction - tracking under INC0010455",
        function="correction",
        confidence=0.9,
    )
    db = _negation_db(case_id, [], added)

    counts = await bridge_conversational_mentions(db, tenant_id, ev)

    assert counts["memberships"] == 1
    assert counts.get("negated") is None
    from contextedge.models.case_bridge import EvidenceCaseMembership

    (row,) = [a for a in added if isinstance(a, EvidenceCaseMembership)]
    assert row.status != "negative"
    assert row.relationship_type == "explicit_reference"


@pytest.mark.asyncio
async def test_cluster_never_pulls_negated_evidence_back_in():
    """A7 resolver fence: evidence explicitly dissociated from a cluster
    anchor case stays out even when a correlation edge would pull it."""
    from contextedge.services.episode_cluster_service import resolve_episode_cluster

    tenant_id = uuid4()
    ticket = uuid4()
    severed = uuid4()
    case_id = uuid4()
    from datetime import UTC, datetime

    now = datetime(2026, 8, 1, 12, 0, 0, tzinfo=UTC)
    call = {"n": 0}

    async def execute(stmt):
        call["n"] += 1
        text = str(stmt)
        result = Mock()
        if "evidence_items" in text and "correlation" not in text:
            if call["n"] == 1:
                result.all.return_value = [(ticket, now)]
            else:
                result.all.return_value = [(severed, now)]
            return result
        if text.startswith("SELECT evidence_case_memberships.canonical_case_id"):
            result.scalars.return_value.all.return_value = [case_id]
            return result
        if text.startswith(
            "SELECT evidence_case_memberships.evidence_id, "
            "evidence_case_memberships.relationship_type"
        ):
            result.all.return_value = []
            return result
        if text.startswith("SELECT evidence_case_memberships.evidence_id"):
            # The negative-evidence fence: severed is dissociated.
            result.scalars.return_value.all.return_value = [severed]
            return result
        if "correlation_edges" in text:
            result.all.return_value = [(ticket, severed, "identity_match")]
            return result
        result.scalars.return_value.all.return_value = []
        result.all.return_value = []
        return result

    cluster = await resolve_episode_cluster(
        SimpleNamespace(execute=execute), tenant_id, [ticket]
    )
    assert severed not in cluster.evidence_ids
    assert cluster.evidence_ids == [ticket]


# --- quoted / forwarded content (A5) ----------------------------------------


def test_quoted_ranges_detects_markers():
    from contextedge.services.ticket_bridge_service import quoted_ranges

    text = "fresh line\n> quoted INC0010427\nmore fresh"
    ranges = quoted_ranges(text)
    assert len(ranges) == 1
    start, end = ranges[0]
    assert "quoted INC0010427" in text[start:end]

    fwd = "see below\n---------- Forwarded message ----------\nINC0010455 stuff"
    ranges = quoted_ranges(fwd)
    assert any("INC0010455" in fwd[s:e] for s, e in ranges)

    outlook = "my reply\nFrom: Alice <a@x.com>\nSent: Monday\nOld body INC0010460"
    ranges = quoted_ranges(outlook)
    assert any("INC0010460" in outlook[s:e] for s, e in ranges)

    assert quoted_ranges("no quotes here") == []
    assert quoted_ranges(None) == []


def test_token_appearing_fresh_and_quoted_counts_as_fresh():
    from contextedge.services.ticket_bridge_service import (
        extract_ticket_tokens_with_spans,
    )

    text = "about INC0010427\n> earlier: INC0010427 and INC0010455"
    pairs = dict(extract_ticket_tokens_with_spans(text))
    assert pairs["INC0010427"] is False  # fresh occurrence wins
    assert pairs["INC0010455"] is True


@pytest.mark.asyncio
async def test_quoted_digest_stays_mentioned_only_fresh_keeps_full():
    """Acceptance: a reply quoting last week's digest gets
    mentioned_only-at-most from the quoted block while the author's own
    sentence keeps full confidence."""
    from contextedge.services.ticket_bridge_service import (
        bridge_conversational_mentions,
    )

    tenant_id = uuid4()
    fresh_case = uuid4()
    quoted_cases = {uuid4(), uuid4(), uuid4()}
    case_by_token = {
        "INC0010001": fresh_case,
        "INC0010427": list(quoted_cases)[0],
        "INC0010455": list(quoted_cases)[1],
        "INC0010460": list(quoted_cases)[2],
    }
    added = []
    body = (
        "please prioritize INC0010001\n"
        "> Weekly review of INC0010427, INC0010455 and INC0010460"
    )
    ev = SimpleNamespace(
        id=uuid4(), title=None, body_text=body, thread_id=None,
        message_function=None, message_function_confidence=None,
    )
    # Bound parameters are invisible in str(stmt); route identifier
    # lookups by call order (the bridge resolves in `located` order).
    order = ["INC0010001", "INC0010427", "INC0010455", "INC0010460"]
    calls = {"i": 0}

    async def execute_ordered(stmt):
        text = str(stmt)
        result = Mock()
        if text.startswith("SELECT case_identifiers.canonical_case_id"):
            token = order[calls["i"]]
            calls["i"] += 1
            result.scalars.return_value.all.return_value = [case_by_token[token]]
            return result
        if "JOIN evidence_items" in text and "canonical_case_id" in text:
            result.scalars.return_value.all.return_value = []
            return result
        result.scalar_one_or_none.return_value = None
        result.scalars.return_value.all.return_value = []
        return result

    db = SimpleNamespace(
        execute=execute_ordered,
        add=added.append,
        flush=AsyncMock(),
        begin_nested=Mock(return_value=_NestedTx()),
    )

    counts = await bridge_conversational_mentions(db, tenant_id, ev)

    from contextedge.models.case_bridge import EvidenceCaseMembership

    rows = [a for a in added if isinstance(a, EvidenceCaseMembership)]
    by_case = {r.canonical_case_id: r for r in rows}
    # Fresh sentence: full-confidence explicit reference (weighted digest
    # count = 1 + 0.5*3 = 2.5 < 3, so no downgrade).
    assert by_case[fresh_case].relationship_type == "explicit_reference"
    # Quoted block: mentioned_only at reduced confidence.
    for qc in quoted_cases:
        assert by_case[qc].relationship_type == "mentioned_only"
        assert by_case[qc].extraction_location == "quoted_body"
    # Quoted mentions never anchor the thread topic.
    assert counts.get("anchor_case_id") == str(fresh_case)
    assert counts["digest_downgraded"] is False


@pytest.mark.asyncio
async def test_quoted_pending_mention_reconciles_as_mentioned_only():
    from contextedge.services.ticket_bridge_service import register_ticket_identifier

    tenant_id = uuid4()
    case_id = uuid4()
    mention = SimpleNamespace(
        evidence_id=uuid4(),
        extraction_location="quoted_body",
        status="pending",
        resolved_case_id=None,
    )
    added = []

    async def execute(stmt):
        text = str(stmt)
        result = Mock()
        if text.startswith("SELECT case_identifiers.id,"):
            result.scalar_one_or_none.return_value = None
            return result
        if text.startswith("SELECT pending_identifier_mentions.id,"):
            result.scalars.return_value.all.return_value = [mention]
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
    counts = await register_ticket_identifier(
        db,
        tenant_id,
        evidence=SimpleNamespace(id=uuid4()),
        source_type="servicenow",
        payload={"number": "INC0010427"},
        canonical_case_id=case_id,
    )
    assert counts["reconciled_mentions"] == 1
    from contextedge.models.case_bridge import EvidenceCaseMembership

    quoted_rows = [
        a
        for a in added
        if isinstance(a, EvidenceCaseMembership)
        and a.relationship_type == "mentioned_only"
    ]
    assert len(quoted_rows) == 1
