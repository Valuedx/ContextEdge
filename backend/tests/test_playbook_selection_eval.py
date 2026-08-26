"""Playbook-ranking eval harness must call the production memory + ranker path."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from contextedge.services.evaluation_service import _execute_evaluation_core
from contextedge.services.memory_service import RuntimeMemoryContext


def _ranked(stable_key, score, keyword=0.0, applicability="unvalidated"):
    return SimpleNamespace(
        playbook=SimpleNamespace(stable_key=stable_key),
        score=score,
        confidence=score,
        confidence_calibrated=score,
        applicability=applicability,
        breakdown={"keyword": keyword},
    )


@pytest.mark.asyncio
async def test_ranking_eval_uses_memory_context_and_passes_filters():
    tenant_id = uuid4()
    domain_id = uuid4()
    run = SimpleNamespace(config={}, results=None, status="running", completed_at=None)
    ds = SimpleNamespace(
        cases=[
            {
                "symptoms": ["vpn login failing"],
                "entities": ["vpn-gw-east-01"],
                "context": "users cannot authenticate",
                "expected_playbook_stable_key": "pb-vpn",
                "domain_id": str(domain_id),
                "max_risk_tier": "high",
                "caller_roles": ["analyst"],
            }
        ]
    )
    memory = RuntimeMemoryContext(
        query_text="vpn login failing vpn-gw-east-01 users cannot authenticate",
        short_term={},
        long_term={},
        reasoning={},
    )
    db = SimpleNamespace(flush=AsyncMock(), refresh=AsyncMock())
    ranked = [
        _ranked("pb-vpn", 0.6, keyword=0.8),
        _ranked("pb-other", 0.4, keyword=0.1),
    ]

    with (
        patch(
            "contextedge.services.evaluation_service.build_runtime_memory_context",
            AsyncMock(return_value=memory),
        ) as mem_mock,
        patch(
            "contextedge.services.evaluation_service.rank_playbooks",
            AsyncMock(return_value=ranked),
        ) as rank_mock,
    ):
        await _execute_evaluation_core(db, run, ds, tenant_id, datetime.now(UTC))

    mem_mock.assert_awaited_once()
    mem_kwargs = mem_mock.await_args.kwargs
    assert mem_kwargs["tenant_id"] == tenant_id
    assert mem_kwargs["symptoms"] == ["vpn login failing"]
    assert mem_kwargs["domain_id"] == domain_id

    rank_kwargs = rank_mock.await_args.kwargs
    assert rank_kwargs["query_text"] == memory.query_text
    assert rank_kwargs["domain_id"] == domain_id
    assert rank_kwargs["max_risk_tier"] == "high"
    assert rank_kwargs["caller_roles"] == ["analyst"]
    assert rank_kwargs["top_k"] == 10

    assert run.results["top1_accuracy"] == 1.0
    assert run.results["recall_at_3"] == 1.0
    assert run.results["recall_at_10"] == 1.0
    assert run.results["mrr"] == 1.0
    assert run.results["abstain_rate"] == 0.0
    assert run.results["keyword_score_zero_rate"] == 0.0
    assert run.results["ece"] is not None
    assert run.results["brier"] is not None
    assert run.results["applicability_precision"] is None
    assert run.results["by_source"]["authored"]["case_count"] == 1
    assert run.results["cases"][0]["query_text"] == memory.query_text


@pytest.mark.asyncio
async def test_ranking_eval_records_abstain_and_keyword_zero():
    tenant_id = uuid4()
    run = SimpleNamespace(config={}, results=None, status="running", completed_at=None)
    ds = SimpleNamespace(
        cases=[
            {
                "symptoms": ["obscure failure"],
                "entities": [],
                "expected_playbook_stable_key": "pb-missing",
            }
        ]
    )
    memory = RuntimeMemoryContext(
        query_text="obscure failure", short_term={}, long_term={}, reasoning={}
    )
    db = SimpleNamespace(flush=AsyncMock(), refresh=AsyncMock())

    with (
        patch(
            "contextedge.services.evaluation_service.build_runtime_memory_context",
            AsyncMock(return_value=memory),
        ),
        patch(
            "contextedge.services.evaluation_service.rank_playbooks",
            AsyncMock(return_value=[]),
        ),
    ):
        await _execute_evaluation_core(db, run, ds, tenant_id, datetime.now(UTC))

    assert run.results["abstain_rate"] == 1.0
    assert run.results["top1_accuracy"] == 0.0
    assert run.results["keyword_score_zero_rate"] is None
