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
    # Provenance is structured, not buried: cited nodes become refs, and
    # the record routes through review rather than landing authoritative.
    assert payload["evidence_refs"] == [
        {
            "ref_type": "issue_signature",
            "ref_id": "abc",
            "description": "cited in the projection that informed this run",
        }
    ]
    assert payload["approval_required"] is True


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


@pytest.mark.asyncio
async def test_in_process_client_threads_scope_and_refs_through():
    """session/domain scoping and evidence refs must reach
    create_decision — dropping them was the provenance hole."""
    from unittest.mock import patch
    from uuid import uuid4

    from contextedge.integrations.maf.client import InProcessDecisionWritebackClient

    class _Ctx:
        def __init__(self):
            self.commit = AsyncMock()
            self.rollback = AsyncMock()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    ctx = _Ctx()
    tenant_id, actor_id, session_id, domain_id = (uuid4() for _ in range(4))
    client = InProcessDecisionWritebackClient(
        lambda: ctx, tenant_id, actor_id, session_id=session_id, domain_id=domain_id
    )
    refs = [{"ref_type": "evidence", "ref_id": "e1", "description": "d"}]
    with patch(
        "contextedge.services.decision_trace_service.create_decision",
        new=AsyncMock(return_value=SimpleNamespace(id=uuid4())),
    ) as create:
        await client.record_decision(
            {"rationale_summary": "r", "evidence_refs": refs}
        )
    kwargs = create.await_args.kwargs
    assert kwargs["session_id"] == session_id
    assert kwargs["domain_id"] == domain_id
    assert kwargs["evidence_refs"] == refs
    assert kwargs["approval_required"] is True
