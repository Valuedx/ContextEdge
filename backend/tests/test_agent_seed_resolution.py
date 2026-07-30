"""Semantic seed resolution for the agent graph projection.

The old resolver matched the WHOLE conversation string with icontains
against playbook/pattern titles — natural sentences never matched, so the
proactive MAF provider usually injected nothing. The new resolver layers
FTS, episode-embedding similarity, and exact identifier matching.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from contextedge.graph.agent.contracts import AgentGraphAccessScope, AgentGraphRequest
from contextedge.graph.agent.repository import (
    SQLAlchemyAgentGraphRepository,
    extract_identifier_tokens,
)


# ---- identifier extraction ----


def test_extracts_operational_identifiers_from_sentences():
    tokens = extract_identifier_tokens(
        "Workflow MG22 completed successfully, but the customer did not "
        "receive the output from vpn-gw-east-01 (see INC0010427, ORDERS_DB)."
    )
    assert "MG22" in tokens
    assert "vpn-gw-east-01" in tokens
    assert "INC0010427" in tokens
    assert "ORDERS_DB" in tokens


def test_extraction_skips_stopwords_and_short_tokens():
    tokens = extract_identifier_tokens("OK so IT said FYI the answer is 5 ASAP")
    assert tokens == []  # "5" too short; caps words are stoplisted


def test_extraction_caps_and_dedupes():
    text = " ".join(f"HOST{i:02d}" for i in range(20)) + " HOST00"
    tokens = extract_identifier_tokens(text)
    assert len(tokens) == 8
    assert tokens[0] == "HOST00"


# ---- resolver plumbing ----


def _scope(tenant_id):
    return AgentGraphAccessScope(
        tenant_id=tenant_id,
        principal_id=uuid4(),
        principal_type="user",
    )


class _RowsResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)

    def scalars(self):
        return SimpleNamespace(all=lambda: [r[0] if isinstance(r, tuple) else r for r in self._rows])


def _dispatching_db(handlers):
    """Fake session: routes each execute by markers in the compiled SQL."""
    executed_sql: list[str] = []

    async def _execute(stmt):
        try:
            sql = str(stmt.compile(dialect=postgresql.dialect()))
        except Exception:
            sql = str(stmt)
        executed_sql.append(sql)
        for marker, result in handlers:
            if marker in sql:
                return result
        return _RowsResult([])

    return SimpleNamespace(execute=AsyncMock(side_effect=_execute)), executed_sql


@pytest.mark.asyncio
async def test_sentence_query_uses_fts_and_semantic_layers():
    tenant_id = uuid4()
    playbook_id = uuid4()
    episode_near = uuid4()
    episode_far = uuid4()

    db, executed_sql = _dispatching_db(
        [
            ("plainto_tsquery", _RowsResult([(playbook_id, 0.7)])),
            ("HALFVEC", _RowsResult([(episode_near, 0.2), (episode_far, 0.7)])),
        ]
    )
    repo = SQLAlchemyAgentGraphRepository(db)

    with patch(
        "contextedge.ai.provider.generate_embedding",
        AsyncMock(return_value=[0.1] * 3072),
    ):
        seeds = await repo.resolve_seeds(
            AgentGraphRequest(
                query="Workflow completed successfully but the customer did not receive the output"
            ),
            _scope(tenant_id),
        )

    reasons = {(s.ref.type, s.reason) for s in seeds}
    # FTS found the playbook even though the sentence is no substring of it.
    assert ("playbook", "query_fts") in reasons
    # Semantic layer: near episode seeded, far one (similarity 0.3) dropped.
    seeded_ids = {s.ref.id for s in seeds}
    assert episode_near in seeded_ids
    assert episode_far not in seeded_ids
    # No whole-sentence icontains anywhere.
    assert not any("LIKE '%" in sql and "completed successfully" in sql for sql in executed_sql)


@pytest.mark.asyncio
async def test_embedding_failure_is_soft_and_other_layers_survive():
    tenant_id = uuid4()
    session_id = uuid4()
    db, _ = _dispatching_db([])
    repo = SQLAlchemyAgentGraphRepository(db)

    with patch(
        "contextedge.ai.provider.generate_embedding",
        AsyncMock(side_effect=RuntimeError("budget exceeded")),
    ):
        seeds = await repo.resolve_seeds(
            AgentGraphRequest(query="the vpn is down again", session_id=session_id),
            _scope(tenant_id),
        )

    assert any(s.ref.type == "session" and s.ref.id == session_id for s in seeds)


@pytest.mark.asyncio
async def test_query_identifiers_match_entities_exactly():
    tenant_id = uuid4()
    entity_id = uuid4()

    db, executed_sql = _dispatching_db(
        [
            ("external_id", _RowsResult([(entity_id,)])),
        ]
    )
    repo = SQLAlchemyAgentGraphRepository(db)

    with patch(
        "contextedge.ai.provider.generate_embedding",
        AsyncMock(side_effect=RuntimeError("no llm in test")),
    ):
        seeds = await repo.resolve_seeds(
            AgentGraphRequest(query="MG22 did not deliver the output"),
            _scope(tenant_id),
        )

    exact = [s for s in seeds if s.reason == "query_identifier_exact"]
    assert exact and exact[0].ref.id == entity_id
    assert exact[0].relevance == 0.95


@pytest.mark.asyncio
async def test_exact_entity_hit_suppresses_icontains_fallback():
    tenant_id = uuid4()
    entity_id = uuid4()
    db, executed_sql = _dispatching_db(
        [("external_id", _RowsResult([(entity_id,)]))]
    )
    repo = SQLAlchemyAgentGraphRepository(db)

    seeds: list = []
    await repo._seed_entity_term(seeds, _scope(tenant_id), "MG22", reason="entity")

    assert [s.ref.id for s in seeds] == [entity_id]
    # Exact hit: the LIKE fallback queries were never issued.
    assert not any("LIKE" in sql.upper() and "entities" in sql for sql in executed_sql)


@pytest.mark.asyncio
async def test_seeds_are_deduplicated_capped_and_sorted():
    tenant_id = uuid4()
    dup = uuid4()
    db, _ = _dispatching_db([])
    repo = SQLAlchemyAgentGraphRepository(db)

    request = AgentGraphRequest(
        seeds=[{"type": "playbook", "id": dup}, {"type": "playbook", "id": dup}],
        session_id=uuid4(),
    )
    with patch(
        "contextedge.ai.provider.generate_embedding",
        AsyncMock(side_effect=RuntimeError("skip")),
    ):
        seeds = await repo.resolve_seeds(request, _scope(tenant_id))

    keys = [s.ref.key for s in seeds]
    assert len(keys) == len(set(keys))
    assert len(seeds) <= 20
    assert seeds == sorted(seeds, key=lambda s: (-s.relevance, s.ref.type, str(s.ref.id)))
