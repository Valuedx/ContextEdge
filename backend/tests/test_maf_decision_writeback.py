"""F1: the MAF agent's diagnostic trail flows back as a governed decision.

Without write-back every diagnosis starts from zero — the graph learns
only from human tickets, never from agent runs. With it, the next agent
facing the same signature inherits what this one concluded, and the
record goes through the SAME decisions path humans use so review and
audit apply identically.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("agent_framework")

from contextedge.integrations.maf.provider import ContextGraphProvider  # noqa: E402


def _provider(writeback):
    return ContextGraphProvider(client=AsyncMock(), writeback=writeback)


@pytest.mark.asyncio
async def test_after_run_records_the_trail():
    writeback = AsyncMock()
    provider = _provider(writeback)
    state = {
        "contextedge_projection": {
            "query": "ssl handshake failure",
            "projection_id": "p-1",
            "cited_nodes": ["issue_signature:abc"],
        }
    }
    await provider.after_run(
        state=state,
        response=SimpleNamespace(text="Enable TLS 1.2 in the REST client plugin config."),
    )
    payload = writeback.record_decision.await_args.args[0]
    assert payload["decision_type"] == "agent_diagnosis"
    assert payload["actor_type"] == "ai"
    assert "TLS 1.2" in payload["rationale_summary"]
    assert payload["context_snapshot"]["cited_nodes"] == ["issue_signature:abc"]


@pytest.mark.asyncio
async def test_no_projection_means_no_writeback():
    """A run the graph never informed has nothing to cite — recording it
    would attribute an answer to context that was not there."""
    writeback = AsyncMock()
    provider = _provider(writeback)
    await provider.after_run(state={}, response=SimpleNamespace(text="hello"))
    writeback.record_decision.assert_not_awaited()


@pytest.mark.asyncio
async def test_writeback_failure_never_breaks_the_run():
    writeback = AsyncMock()
    writeback.record_decision.side_effect = RuntimeError("api down")
    provider = _provider(writeback)
    await provider.after_run(
        state={"contextedge_projection": {"query": "q", "projection_id": "p", "cited_nodes": []}},
        response=SimpleNamespace(text="an answer"),
    )  # must not raise


@pytest.mark.asyncio
async def test_no_writeback_client_is_a_noop():
    provider = ContextGraphProvider(client=AsyncMock())
    await provider.after_run(
        state={"contextedge_projection": {"query": "q"}},
        response=SimpleNamespace(text="x"),
    )  # must not raise
