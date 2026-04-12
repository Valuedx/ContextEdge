from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from contextedge.api.v1 import runtime
from contextedge.search.hybrid_ranker import RankedPlaybook
from contextedge.schemas.playbook import RuntimeMatchRequest

from .conftest import make_user


def _request_with_redis():
    redis = SimpleNamespace(setex=AsyncMock())
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(redis=redis))), redis


def _memory_context(query_text: str, trace_event_count: int = 0):
    summary = {
        "short_term": {"recent_evidence_count": 0},
        "long_term": {"resolved_identity_count": 0},
        "reasoning": {"trace_event_count": trace_event_count},
    }
    return SimpleNamespace(
        query_text=query_text,
        reasoning=summary["reasoning"],
        filters_payload=lambda: {
            "memory_classes": ["short_term", "long_term", "reasoning"],
            "memory_summary": summary,
        },
    )


@pytest.mark.asyncio
async def test_runtime_match_with_session_records_trace():
    session_id = uuid4()
    playbook = SimpleNamespace(
        id=uuid4(),
        title="Restart DB",
        stable_key="pb-123",
        risk_tier="high",
        automation_mode="suggest_only",
    )
    ranked = [
        RankedPlaybook(
            playbook=playbook,
            score=0.8123,
            confidence=0.8123,
            playbook_confidence=0.67,
            freshness_status="fresh",
            evidence_count=4,
            breakdown={"keyword": 0.5},
        )
    ]
    request, redis = _request_with_redis()

    with (
        patch.object(runtime, "build_runtime_memory_context", AsyncMock(return_value=_memory_context("timeout postgres session note", 1))),
        patch.object(runtime, "rank_playbooks", AsyncMock(return_value=ranked)),
        patch.object(runtime, "append_trace_event", AsyncMock(return_value=SimpleNamespace(id=uuid4()))) as trace_mock,
        patch.object(runtime, "append_operational_event", AsyncMock()) as event_mock,
    ):
        response = await runtime.runtime_match(
            request=request,
            body=RuntimeMatchRequest(
                symptoms=["timeout"],
                entities=["postgres"],
                session_id=session_id,
            ),
            db=SimpleNamespace(),
            user=make_user(roles=["knowledge_manager"]),
        )

    assert response.session_id == session_id
    assert response.results[0].retrieval_score == 0.8123
    assert response.results[0].playbook_confidence == 0.67
    assert response.filters_applied["memory_classes"] == ["short_term", "long_term", "reasoning"]
    trace_mock.assert_awaited_once()
    event_mock.assert_awaited_once()
    redis.setex.assert_awaited_once()


@pytest.mark.asyncio
async def test_runtime_match_without_session_skips_trace_write():
    playbook = SimpleNamespace(
        id=uuid4(),
        title="Clear Cache",
        stable_key="pb-456",
        risk_tier="medium",
        automation_mode="suggest_only",
    )
    ranked = [
        RankedPlaybook(
            playbook=playbook,
            score=0.6234,
            confidence=0.6234,
            playbook_confidence=0.51,
            freshness_status="aging",
            evidence_count=2,
            breakdown={"semantic": 0.4},
        )
    ]
    request, _redis = _request_with_redis()

    with (
        patch.object(runtime, "build_runtime_memory_context", AsyncMock(return_value=_memory_context("slow cache", 0))),
        patch.object(runtime, "rank_playbooks", AsyncMock(return_value=ranked)),
        patch.object(runtime, "append_trace_event", AsyncMock()) as trace_mock,
        patch.object(runtime, "append_operational_event", AsyncMock()) as event_mock,
    ):
        response = await runtime.runtime_match(
            request=request,
            body=RuntimeMatchRequest(symptoms=["slow"], entities=["cache"]),
            db=SimpleNamespace(),
            user=make_user(roles=["knowledge_manager"]),
        )

    assert response.session_id is None
    assert response.results[0].retrieval_score == 0.6234
    assert response.results[0].playbook_confidence == 0.51
    assert response.filters_applied["memory_summary"]["reasoning"]["trace_event_count"] == 0
    trace_mock.assert_not_awaited()
    event_mock.assert_awaited_once()
