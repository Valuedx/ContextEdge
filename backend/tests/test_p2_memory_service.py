from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from contextedge.services.memory_service import (
    LONG_TERM_MEMORY,
    REASONING_MEMORY,
    SHORT_TERM_MEMORY,
    build_runtime_memory_context,
    classify_evidence_memory_class,
    memory_retention_windows,
)
from contextedge.services.pattern_service import create_pattern_from_episodes
from contextedge.services.retention_service import apply_retention_policy


class _ScalarOneOrNoneResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _ScalarsResult:
    def __init__(self, values):
        self._values = values

    def scalars(self):
        return SimpleNamespace(all=lambda: self._values)


class _AllResult:
    def __init__(self, values):
        self._values = values

    def all(self):
        return self._values


class _ScalarValueResult:
    def __init__(self, value):
        self._value = value

    def scalar(self):
        return self._value


def test_memory_retention_windows_expand_long_term_and_reasoning():
    windows = memory_retention_windows(30)

    assert windows[SHORT_TERM_MEMORY] == 30
    assert windows[LONG_TERM_MEMORY] >= 180
    assert windows[REASONING_MEMORY] >= 90


def test_classify_evidence_memory_class_prefers_long_term_signals():
    kb_item = SimpleNamespace(evidence_type="kb_article", canonical_entity_refs=None)
    identity_item = SimpleNamespace(
        evidence_type="message",
        canonical_entity_refs={"identities": [{"canonical_id": str(uuid4())}]},
    )
    short_item = SimpleNamespace(evidence_type="message", canonical_entity_refs=None)

    assert classify_evidence_memory_class(kb_item) == LONG_TERM_MEMORY
    assert classify_evidence_memory_class(identity_item) == LONG_TERM_MEMORY
    assert classify_evidence_memory_class(short_item) == SHORT_TERM_MEMORY


@pytest.mark.asyncio
async def test_build_runtime_memory_context_summarizes_memory_classes():
    tenant_id = uuid4()
    session_id = uuid4()
    trace_event = SimpleNamespace(event_type="retrieve", reasoning="Checked known outage", confidence=0.7)
    session = SimpleNamespace(
        id=session_id,
        status="open",
        symptoms=["timeout"],
        entities=["postgres"],
        external_case_ids=["INC-1"],
        notes="customer reports recurring issue",
        trace_events=[trace_event],
    )
    run = SimpleNamespace(
        approval_requests=[SimpleNamespace(status="pending")],
        step_runs=[SimpleNamespace(tool_invocations=[SimpleNamespace(tool_name="ping")])],
    )
    evidence = SimpleNamespace(id=uuid4(), ingested_at=datetime.now(timezone.utc))
    identity = SimpleNamespace(id=uuid4(), canonical_name="sql-prod", entity_type="service")
    db = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                _ScalarOneOrNoneResult(session),
                _ScalarsResult([run]),
                _ScalarsResult([]),
                _ScalarsResult([identity]),
                _ScalarsResult([evidence]),
                _ScalarValueResult(4),
                _ScalarValueResult(2),
            ]
        )
    )

    with patch(
        "contextedge.services.memory_service.resolve_identity_ids_for_terms",
        AsyncMock(return_value={identity.id}),
    ):
        context = await build_runtime_memory_context(
            db,
            tenant_id=tenant_id,
            symptoms=["timeout"],
            entities=["postgres"],
            context="during backup",
            session_id=session_id,
            domain_id=None,
        )

    assert "customer reports recurring issue" in context.query_text
    assert context.short_term["session"]["session_id"] == str(session_id)
    assert context.long_term["resolved_identity_count"] == 1
    assert context.reasoning["pending_approval_count"] == 1
    assert context.reasoning["recent_tools"] == ["ping"]


@pytest.mark.asyncio
async def test_create_pattern_from_episodes_promotes_long_term_memory():
    tenant_id = uuid4()
    episode_ids = [uuid4(), uuid4()]
    added = []
    # Service now (a) runs the domain-safety membership check, (b) calls
    # persist_pattern_enrichment_edges, (c) queries db.execute(
    # select(Episode)…) to fetch episode entity_refs, then (d) calls
    # build_episode_graph per episode. Mock each piece so the test
    # focuses on the memory-promotion contract it originally covered.
    membership_result = SimpleNamespace(
        all=lambda: [(episode_id, tenant_id, None) for episode_id in episode_ids],
    )
    episodes_result = SimpleNamespace(
        scalars=lambda: SimpleNamespace(all=lambda: []),
    )
    db = SimpleNamespace(
        add=lambda obj: added.append(obj),
        flush=AsyncMock(),
        refresh=AsyncMock(),
        execute=AsyncMock(side_effect=[membership_result, episodes_result]),
    )

    with (
        patch(
            "contextedge.services.pattern_service.persist_pattern_enrichment_edges",
            AsyncMock(),
        ),
        patch(
            "contextedge.services.pattern_service.build_episode_graph",
            AsyncMock(),
        ),
        patch(
            "contextedge.services.pattern_service.promote_pattern_memory",
            AsyncMock(),
        ) as promote_mock,
    ):
        pattern = await create_pattern_from_episodes(
            db,
            tenant_id=tenant_id,
            domain_id=None,
            title="DB recurring timeout",
            episode_ids=episode_ids,
        )

    assert pattern.title == "DB recurring timeout"
    promote_mock.assert_awaited_once()
    assert promote_mock.await_args.kwargs["episode_ids"] == episode_ids


@pytest.mark.asyncio
async def test_retention_uses_extended_window_for_long_term_evidence():
    tenant_id = uuid4()
    short_term_item = SimpleNamespace(
        relevance_state="relevant",
        ingested_at=datetime.now(timezone.utc) - timedelta(days=40),
        evidence_type="message",
        canonical_entity_refs=None,
        sensitivity_label=None,
    )
    long_term_item = SimpleNamespace(
        relevance_state="relevant",
        ingested_at=datetime.now(timezone.utc) - timedelta(days=40),
        evidence_type="kb_article",
        canonical_entity_refs=None,
        sensitivity_label=None,
    )
    db_result = SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [short_term_item, long_term_item]))
    db = SimpleNamespace(
        execute=AsyncMock(return_value=db_result),
        flush=AsyncMock(),
    )

    archived = await apply_retention_policy(
        db,
        tenant_id=tenant_id,
        retention_days=30,
    )

    assert archived == 1
    assert short_term_item.relevance_state == "archived"
    assert long_term_item.relevance_state == "relevant"
