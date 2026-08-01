"""C5 attribution-rate evaluation: the episode_citation dataset kind."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from contextedge.services.evaluation_service import (
    _execute_evaluation_core,
    _run_citation_case,
)


@pytest.mark.asyncio
async def test_citation_case_scores_unsupported_and_wrong_attribution():
    tenant_id = uuid4()
    ev_a, ev_b = str(uuid4()), str(uuid4())
    case = {
        "kind": "episode_citation",
        "evidence_items": [
            {"title": "INC", "body": "cert expired", "evidence_id": ev_a},
            {"title": "Teams", "body": "renewed", "evidence_id": ev_b},
        ],
        "gold_step_citations": [[ev_a], [ev_b], [ev_b]],
    }
    predicted = [
        {
            "title": "VPN outage",
            "steps": [
                {"evidence_refs": [ev_a]},        # correct
                {"evidence_refs": [ev_a]},        # wrong source (gold: ev_b)
                {"evidence_refs": None},          # unsupported
            ],
        }
    ]
    with patch(
        "contextedge.ai.extractors.episode_extractor.reconstruct_episode",
        AsyncMock(return_value=predicted),
    ):
        result = await _run_citation_case(SimpleNamespace(), tenant_id, case, None)

    assert result["unsupported_steps"] == 1
    assert result["unsupported_step_rate"] == round(1 / 3, 3)
    assert result["compared_steps"] == 3
    assert result["wrong_attribution"] == 1
    assert result["wrong_attribution_rate"] == round(1 / 3, 3)


@pytest.mark.asyncio
async def test_mixed_dataset_aggregates_citation_block():
    from datetime import UTC, datetime

    tenant_id = uuid4()
    ev_a = str(uuid4())
    run = SimpleNamespace(
        config={"episode_prompt_version": "v2"},
        results=None,
        status="running",
        completed_at=None,
    )
    ds = SimpleNamespace(
        cases=[
            {
                "kind": "episode_citation",
                "evidence_items": [{"evidence_id": ev_a}],
                "gold_step_citations": [[ev_a]],
            }
        ]
    )
    predicted = [{"title": "E", "steps": [{"evidence_refs": [ev_a]}]}]
    db = SimpleNamespace(flush=AsyncMock(), refresh=AsyncMock())

    with patch(
        "contextedge.ai.extractors.episode_extractor.reconstruct_episode",
        AsyncMock(return_value=predicted),
    ) as recon:
        await _execute_evaluation_core(
            db, run, ds, tenant_id, datetime.now(UTC)
        )

    assert run.status == "completed"
    citation = run.results["citation"]
    assert citation["case_count"] == 1
    assert citation["episode_prompt_version"] == "v2"
    assert citation["mean_unsupported_step_rate"] == 0.0
    assert citation["mean_wrong_attribution_rate"] == 0.0
    # The pinned version reached the extractor.
    assert recon.await_args.kwargs["prompt_version"] == "v2"


def test_prompt_version_accessor_pins_and_raises():
    from contextedge.ai.prompts import get_prompt_version

    assert get_prompt_version("episode", "v2").version == "v2"
    with pytest.raises(KeyError):
        get_prompt_version("episode", "v99")
