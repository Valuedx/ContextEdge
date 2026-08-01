"""A3 thread-topic state: anchoring rules, provisional unification,
per-message inheritance, abstention."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from contextedge.models.case_bridge import EvidenceCaseMembership
from contextedge.models.thread_topic import ThreadTopic
from contextedge.services.thread_topic_service import (
    apply_thread_topic,
    set_thread_topic,
)


class _NestedTx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


def _topic_db(
    *,
    existing_topic=None,
    thread_evidence=(),
    anchored_ids=(),
    negated_cases=(),
    added=None,
):
    added = added if added is not None else []

    async def execute(stmt):
        text = str(stmt)
        result = Mock()
        if text.startswith("SELECT thread_topics."):
            result.scalar_one_or_none.return_value = existing_topic
            return result
        if "JOIN evidence_items" in text and "canonical_case_id" in text:
            result.scalars.return_value.all.return_value = list(negated_cases)
            return result
        if text.startswith("SELECT evidence_items."):
            result.scalars.return_value.all.return_value = list(thread_evidence)
            return result
        if text.startswith("SELECT evidence_case_memberships.evidence_id"):
            result.scalars.return_value.all.return_value = list(anchored_ids)
            return result
        if text.startswith("SELECT evidence_case_memberships.id"):
            result.scalar_one_or_none.return_value = (
                anchored_ids[0] if anchored_ids else None
            )
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


def _msg(thread_id, function=None):
    return SimpleNamespace(
        id=uuid4(),
        thread_id=thread_id,
        title=None,
        body_text="any update?",
        message_function=function,
        message_function_confidence=0.9 if function else None,
    )


@pytest.mark.asyncio
async def test_first_anchor_sets_topic_and_unifies_prior_messages():
    """The 40-message acceptance: pre-anchor messages attach at anchor
    time via the unification sweep."""
    tenant_id = uuid4()
    thread = uuid4()
    case = uuid4()
    early_1, early_2 = _msg(thread), _msg(thread)
    db, added = _topic_db(thread_evidence=[early_1, early_2])

    counts = await set_thread_topic(
        db, tenant_id, thread, case, provisional=False, set_by="anchor"
    )

    assert counts["set"] is True
    assert counts["unified"] == 2
    topics = [a for a in added if isinstance(a, ThreadTopic)]
    assert len(topics) == 1 and topics[0].is_provisional is False
    memberships = [a for a in added if isinstance(a, EvidenceCaseMembership)]
    assert {m.evidence_id for m in memberships} == {early_1.id, early_2.id}
    assert all(m.relationship_type == "thread_topic" for m in memberships)


@pytest.mark.asyncio
async def test_provisional_promotes_and_unifies_on_real_anchor():
    tenant_id = uuid4()
    thread = uuid4()
    provisional_case, real_case = uuid4(), uuid4()
    topic = ThreadTopic(
        tenant_id=tenant_id,
        thread_id=thread,
        canonical_case_id=provisional_case,
        is_provisional=True,
        set_by="thread_seed",
    )
    orphan = _msg(thread)
    db, added = _topic_db(existing_topic=topic, thread_evidence=[orphan])

    counts = await set_thread_topic(
        db, tenant_id, thread, real_case, provisional=False, set_by="anchor"
    )

    assert counts["set"] is True
    assert topic.is_provisional is False
    assert topic.canonical_case_id == real_case
    assert counts["unified"] == 1


@pytest.mark.asyncio
async def test_competing_plain_anchor_never_steals_topic():
    """Abstention: only corrections re-seat an anchored topic."""
    tenant_id = uuid4()
    thread = uuid4()
    current, competing = uuid4(), uuid4()
    topic = ThreadTopic(
        tenant_id=tenant_id,
        thread_id=thread,
        canonical_case_id=current,
        is_provisional=False,
        set_by="anchor",
    )
    db, added = _topic_db(existing_topic=topic)

    counts = await set_thread_topic(
        db, tenant_id, thread, competing, provisional=False, set_by="anchor"
    )

    assert counts["set"] is False
    assert topic.canonical_case_id == current


@pytest.mark.asyncio
async def test_correction_reseats_topic_with_event(monkeypatch):
    tenant_id = uuid4()
    thread = uuid4()
    old_case, new_case = uuid4(), uuid4()
    topic = ThreadTopic(
        tenant_id=tenant_id,
        thread_id=thread,
        canonical_case_id=old_case,
        is_provisional=False,
        set_by="anchor",
    )
    events = []

    async def fake_event(db, **kwargs):
        events.append(kwargs)

    monkeypatch.setattr(
        "contextedge.services.event_log_service.append_operational_event", fake_event
    )
    db, added = _topic_db(existing_topic=topic)

    counts = await set_thread_topic(
        db, tenant_id, thread, new_case, provisional=False, set_by="correction"
    )

    assert counts["changed"] is True
    assert topic.canonical_case_id == new_case
    assert events[0]["event_type"] == "thread.topic_changed"


@pytest.mark.asyncio
async def test_provisional_signal_never_demotes_anchored_topic():
    tenant_id = uuid4()
    thread = uuid4()
    topic = ThreadTopic(
        tenant_id=tenant_id,
        thread_id=thread,
        canonical_case_id=uuid4(),
        is_provisional=False,
        set_by="anchor",
    )
    db, _ = _topic_db(existing_topic=topic)
    counts = await set_thread_topic(
        db, tenant_id, thread, uuid4(), provisional=True, set_by="thread_seed"
    )
    assert counts["set"] is False
    assert topic.is_provisional is False


@pytest.mark.asyncio
async def test_unanchored_message_inherits_topic():
    tenant_id = uuid4()
    thread = uuid4()
    case = uuid4()
    topic = ThreadTopic(
        tenant_id=tenant_id,
        thread_id=thread,
        canonical_case_id=case,
        is_provisional=False,
        set_by="anchor",
        confidence=0.75,
    )
    db, added = _topic_db(existing_topic=topic)

    counts = await apply_thread_topic(db, tenant_id, _msg(thread))

    assert counts["applied"] == 1
    (m,) = [a for a in added if isinstance(a, EvidenceCaseMembership)]
    assert m.canonical_case_id == case
    assert m.relationship_type == "thread_topic"


@pytest.mark.asyncio
async def test_inheritance_skips_provisional_dissociative_and_negated():
    tenant_id = uuid4()
    thread = uuid4()
    case = uuid4()

    provisional = ThreadTopic(
        tenant_id=tenant_id, thread_id=thread, canonical_case_id=case,
        is_provisional=True, set_by="thread_seed",
    )
    db, _ = _topic_db(existing_topic=provisional)
    assert (await apply_thread_topic(db, tenant_id, _msg(thread)))["skipped"] == (
        "no_anchored_topic"
    )

    anchored = ThreadTopic(
        tenant_id=tenant_id, thread_id=thread, canonical_case_id=case,
        is_provisional=False, set_by="anchor",
    )
    db, _ = _topic_db(existing_topic=anchored)
    dissociative = _msg(thread, function="dissociation")
    assert (await apply_thread_topic(db, tenant_id, dissociative))["skipped"] == (
        "dissociative"
    )

    db, _ = _topic_db(existing_topic=anchored, negated_cases=[case])
    assert (await apply_thread_topic(db, tenant_id, _msg(thread)))["skipped"] == (
        "negated"
    )

    db, _ = _topic_db(existing_topic=anchored, anchored_ids=[uuid4()])
    assert (await apply_thread_topic(db, tenant_id, _msg(thread)))["skipped"] == (
        "already_anchored"
    )
