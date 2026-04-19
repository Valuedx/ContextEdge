"""Tests for C3: decision embedding column + semantic similar-decision retrieval."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest


# =========================================================================
# Column + helper shape
# =========================================================================


def test_decision_model_has_embedding_column():
    from contextedge.models.decision import Decision
    cols = {c.name for c in Decision.__table__.columns}
    assert "embedding" in cols


@pytest.mark.asyncio
async def test_embed_decision_empty_returns_zero_vector():
    """All-empty inputs must produce a zero vector so .is_not(None) gates
    still work — matches the embed_evidence convention."""
    from contextedge.ai.embeddings import embed_decision

    emb = await embed_decision(None, None, None)
    assert emb == [0.0] * 3072


@pytest.mark.asyncio
@patch("contextedge.ai.embeddings.generate_embedding", new_callable=AsyncMock)
async def test_embed_decision_combines_type_trace_rationale(mock_gen):
    """Text passed to the provider combines decision_type + compact_trace +
    rationale_summary with newline separators, so semantic retrieval can
    match on all three surfaces."""
    from contextedge.ai.embeddings import embed_decision

    mock_gen.return_value = [0.1] * 3072

    await embed_decision(
        decision_type="execute_playbook",
        rationale_summary="Hybrid ranking matched VPN certificate rotation",
        compact_trace="VPN cert rotation",
    )
    assert mock_gen.await_count == 1
    text = mock_gen.call_args.args[0]
    assert "execute_playbook" in text
    assert "VPN cert rotation" in text
    assert "Hybrid ranking" in text


@pytest.mark.asyncio
@patch("contextedge.ai.embeddings.generate_embedding", new_callable=AsyncMock)
async def test_embed_decision_truncates_long_rationale(mock_gen):
    """Rationale is truncated to 6000 chars so a runaway text doesn't blow
    the provider token budget."""
    from contextedge.ai.embeddings import embed_decision

    mock_gen.return_value = [0.1] * 3072
    long_text = "A" * 20000

    await embed_decision(
        decision_type="x",
        rationale_summary=long_text,
    )
    text = mock_gen.call_args.args[0]
    # decision_type + \n\n + truncated rationale
    assert len(text) <= 6100


# =========================================================================
# create_decision inline embedding with graceful fail
# =========================================================================


class _ScalarOneOrNone:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _Scalars:
    def __init__(self, values):
        self._values = values

    def all(self):
        return self._values


class _ScalarsWrap:
    def __init__(self, values):
        self._values = values

    def scalars(self):
        return _Scalars(self._values)


def _make_db():
    added: list = []
    return (
        SimpleNamespace(
            execute=AsyncMock(),
            add=lambda obj: added.append(obj),
            flush=AsyncMock(),
            get=AsyncMock(return_value=None),
            refresh=AsyncMock(),
        ),
        added,
    )


@pytest.mark.asyncio
@patch("contextedge.services.review_queue_service.invalidate_review_context", new_callable=AsyncMock)
@patch("contextedge.ai.embeddings.embed_decision", new_callable=AsyncMock)
@patch("contextedge.services.decision_trace_service.append_operational_event", new_callable=AsyncMock)
@patch("contextedge.services.decision_trace_service.append_trace_event", new_callable=AsyncMock)
async def test_create_decision_stores_embedding(
    mock_trace, mock_op_event, mock_embed, mock_invalidate,
):
    """create_decision calls embed_decision and stamps the result onto the row."""
    from contextedge.models.decision import Decision
    from contextedge.services.decision_trace_service import create_decision

    mock_embed.return_value = [0.5] * 3072

    db, added = _make_db()

    await create_decision(
        db,
        tenant_id=uuid4(),
        decision_type="execute_playbook",
        agent_step="remediation",
        rationale_summary="VPN cert rotation matched",
        compact_trace="VPN cert rotation",
    )

    decisions = [o for o in added if isinstance(o, Decision)]
    assert len(decisions) == 1
    assert decisions[0].embedding == [0.5] * 3072
    mock_embed.assert_awaited_once()


@pytest.mark.asyncio
@patch("contextedge.services.review_queue_service.invalidate_review_context", new_callable=AsyncMock)
@patch("contextedge.ai.embeddings.embed_decision", new_callable=AsyncMock)
@patch("contextedge.services.decision_trace_service.append_operational_event", new_callable=AsyncMock)
@patch("contextedge.services.decision_trace_service.append_trace_event", new_callable=AsyncMock)
async def test_create_decision_swallows_embed_failures(
    mock_trace, mock_op_event, mock_embed, mock_invalidate,
):
    """LLM provider hiccup must not fail decision creation — the row still
    lands; embedding stays null until re-embedded."""
    from contextedge.models.decision import Decision
    from contextedge.services.decision_trace_service import create_decision

    mock_embed.side_effect = RuntimeError("provider 503")

    db, added = _make_db()

    # Should not raise.
    decision = await create_decision(
        db,
        tenant_id=uuid4(),
        decision_type="classify_issue",
        agent_step="diagnostics",
        rationale_summary="Test",
    )
    assert isinstance(decision, Decision)
    assert decision.embedding is None


# =========================================================================
# _resolve_query_embedding
# =========================================================================


@pytest.mark.asyncio
async def test_resolve_query_embedding_prefers_decision_id_over_text():
    from contextedge.services.decision_trace_service import _resolve_query_embedding

    tenant_id = uuid4()
    decision_id = uuid4()
    ref_vec = [0.42] * 3072

    async def _execute(stmt):
        return _ScalarOneOrNone(ref_vec)

    db = SimpleNamespace(execute=_execute)

    with patch(
        "contextedge.ai.provider.generate_embedding",
        new_callable=AsyncMock,
    ) as gen_mock:
        out = await _resolve_query_embedding(
            db,
            tenant_id=tenant_id,
            query_decision_id=decision_id,
            query_text="ignored because id wins",
        )

    assert out == ref_vec
    # The decision lookup wins; no on-the-fly embedding call.
    gen_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_resolve_query_embedding_falls_back_to_text():
    from contextedge.services.decision_trace_service import _resolve_query_embedding

    tenant_id = uuid4()

    async def _execute(stmt):
        return _ScalarOneOrNone(None)

    db = SimpleNamespace(execute=_execute)

    with patch(
        "contextedge.ai.provider.generate_embedding",
        new_callable=AsyncMock,
    ) as gen_mock:
        gen_mock.return_value = [0.9] * 3072
        out = await _resolve_query_embedding(
            db,
            tenant_id=tenant_id,
            query_decision_id=None,
            query_text="VPN auth cert expired",
        )

    assert out == [0.9] * 3072


@pytest.mark.asyncio
async def test_resolve_query_embedding_returns_none_when_nothing_to_embed():
    from contextedge.services.decision_trace_service import _resolve_query_embedding

    db = SimpleNamespace(execute=AsyncMock())
    out = await _resolve_query_embedding(
        db,
        tenant_id=uuid4(),
        query_decision_id=None,
        query_text=None,
    )
    assert out is None


@pytest.mark.asyncio
async def test_resolve_query_embedding_returns_none_on_provider_failure():
    """Provider failure falls back to JSONB ordering — never raises."""
    from contextedge.services.decision_trace_service import _resolve_query_embedding

    db = SimpleNamespace(execute=AsyncMock())

    with patch(
        "contextedge.ai.provider.generate_embedding",
        new_callable=AsyncMock,
    ) as gen_mock:
        gen_mock.side_effect = RuntimeError("rate limited")
        out = await _resolve_query_embedding(
            db,
            tenant_id=uuid4(),
            query_decision_id=None,
            query_text="vpn",
        )
    assert out is None


@pytest.mark.asyncio
async def test_resolve_query_embedding_returns_none_when_referenced_has_no_embedding():
    """If the referenced decision has embedding=NULL, return None so caller
    falls back to created_at ordering rather than querying with a null vector."""
    from contextedge.services.decision_trace_service import _resolve_query_embedding

    async def _execute(stmt):
        return _ScalarOneOrNone(None)

    db = SimpleNamespace(execute=_execute)
    out = await _resolve_query_embedding(
        db,
        tenant_id=uuid4(),
        query_decision_id=uuid4(),
        query_text=None,
    )
    assert out is None


# =========================================================================
# find_similar_decisions — semantic vs fallback ordering
# =========================================================================


@pytest.mark.asyncio
async def test_find_similar_decisions_falls_back_when_no_query_embedding():
    """No query_decision_id + no query_text → created_at DESC ordering."""
    from contextedge.services.decision_trace_service import find_similar_decisions

    captured = {}

    async def _execute(stmt):
        captured["sql"] = str(stmt.compile(compile_kwargs={"literal_binds": False}))
        return _ScalarsWrap([])

    db = SimpleNamespace(execute=_execute)
    await find_similar_decisions(
        db,
        tenant_id=uuid4(),
        decision_type="execute_playbook",
    )

    sql_upper = captured["sql"].upper()
    assert "ORDER BY" in sql_upper
    assert "CREATED_AT DESC" in sql_upper
    # The cosine-distance operator <=> must NOT appear when no embedding.
    assert "<=>" not in captured["sql"]


@pytest.mark.asyncio
@patch("contextedge.services.decision_trace_service._resolve_query_embedding", new_callable=AsyncMock)
async def test_find_similar_decisions_uses_cosine_when_query_available(mock_resolve):
    """Query embedding present → order by embedding <=> query, filter out
    rows with embedding IS NULL."""
    from contextedge.services.decision_trace_service import find_similar_decisions

    mock_resolve.return_value = [0.1] * 3072

    captured = {}

    async def _execute(stmt):
        captured["sql"] = str(stmt)
        return _ScalarsWrap([])

    db = SimpleNamespace(execute=_execute)
    await find_similar_decisions(
        db,
        tenant_id=uuid4(),
        decision_type="execute_playbook",
        query_text="VPN cert rotation",
    )

    sql = captured["sql"]
    assert "<=>" in sql  # pgvector cosine-distance operator
    assert "embedding IS NOT NULL" in sql
    # Fallback ordering must not engage.
    assert "created_at DESC" not in sql


@pytest.mark.asyncio
@patch("contextedge.services.decision_trace_service._resolve_query_embedding", new_callable=AsyncMock)
async def test_find_similar_decisions_excludes_self_when_query_decision_id_passed(mock_resolve):
    """`query_decision_id = X` must not return X itself as a similar result."""
    from contextedge.services.decision_trace_service import find_similar_decisions

    mock_resolve.return_value = [0.2] * 3072

    captured = {}

    async def _execute(stmt):
        captured["sql"] = str(stmt)
        return _ScalarsWrap([])

    db = SimpleNamespace(execute=_execute)
    the_id = uuid4()
    await find_similar_decisions(
        db,
        tenant_id=uuid4(),
        decision_type="execute_playbook",
        query_decision_id=the_id,
    )

    sql = captured["sql"]
    # The self-exclusion predicate `decisions.id != :id_1` must be present.
    assert "decisions.id !=" in sql


@pytest.mark.asyncio
@patch("contextedge.services.decision_trace_service._resolve_query_embedding", new_callable=AsyncMock)
async def test_find_similar_decisions_applies_jsonb_prefilter_in_semantic_mode(mock_resolve):
    """JSONB containment stays as a structural pre-filter even when the
    ordering is semantic — `workflow=vpn` still narrows the candidate set."""
    from contextedge.services.decision_trace_service import find_similar_decisions

    mock_resolve.return_value = [0.3] * 3072

    captured = {}

    async def _execute(stmt):
        captured["sql"] = str(stmt)
        return _ScalarsWrap([])

    db = SimpleNamespace(execute=_execute)
    await find_similar_decisions(
        db,
        tenant_id=uuid4(),
        decision_type="execute_playbook",
        context_snapshot={"workflow": "vpn"},
        query_text="VPN cert rotation",
    )

    sql = captured["sql"]
    # Cosine order + JSONB containment coexist.
    assert "<=>" in sql
    assert "@>" in sql


# =========================================================================
# find_similar_decisions_aggregate passes query params through
# =========================================================================


@pytest.mark.asyncio
@patch("contextedge.services.decision_trace_service.get_decision_effectiveness", new_callable=AsyncMock)
@patch("contextedge.services.decision_trace_service.count_similar_decisions", new_callable=AsyncMock)
@patch("contextedge.services.decision_trace_service.find_similar_decisions", new_callable=AsyncMock)
async def test_aggregate_forwards_semantic_query_params(mock_find, mock_count, mock_eff):
    from contextedge.services.decision_trace_service import find_similar_decisions_aggregate

    mock_find.return_value = []
    mock_count.return_value = 0
    mock_eff.return_value = {"outcomes": {}}

    db = SimpleNamespace()
    query_id = uuid4()
    await find_similar_decisions_aggregate(
        db,
        tenant_id=uuid4(),
        decision_type="execute_playbook",
        query_decision_id=query_id,
        query_text="ignored",
    )

    find_kwargs = mock_find.call_args.kwargs
    assert find_kwargs["query_decision_id"] == query_id
    assert find_kwargs["query_text"] == "ignored"
    # Count + effectiveness stay structural — must NOT receive semantic params.
    assert "query_decision_id" not in mock_count.call_args.kwargs
    assert "query_decision_id" not in mock_eff.call_args.kwargs
