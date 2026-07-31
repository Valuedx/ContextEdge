"""Semantic seed resolution for the agent graph projection.

The old resolver matched the WHOLE conversation string with icontains
against playbook/pattern titles — natural sentences never matched, so the
proactive MAF provider usually injected nothing. The new resolver layers
OR-composed FTS, episode-embedding similarity (savepointed), and exact
identifier matching.
"""

from contextlib import asynccontextmanager
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


def test_extracts_full_email_addresses():
    """Emails are the canonical identity alias form — they must survive
    tokenization whole, not split at the @."""
    tokens = extract_identifier_tokens("Please ask jsmith@acme.com to rerun MG22")
    assert "jsmith@acme.com" in tokens
    assert "acme.com" not in tokens


def test_extraction_requires_a_letter():
    """Bare years and counts are noise, not identifiers."""
    tokens = extract_identifier_tokens("upgrade to version 3.2.1 in 2026, 123 tickets")
    assert "3.2.1" not in tokens
    assert "2026" not in tokens
    assert "123" not in tokens


def test_extraction_skips_stopwords_and_short_tokens():
    tokens = extract_identifier_tokens("OK so IT said FYI the answer is 5 ASAP")
    assert tokens == []


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
        return SimpleNamespace(
            all=lambda: [r[0] if isinstance(r, tuple) else r for r in self._rows]
        )


def _dispatching_db(handlers, *, fail_markers=()):
    """Fake session: routes each execute by markers in the compiled SQL.
    Statements matching *fail_markers* raise (simulating a DB error)."""
    executed_sql: list[str] = []
    state = SimpleNamespace(nested_calls=0)

    async def _execute(stmt):
        try:
            sql = str(stmt.compile(dialect=postgresql.dialect()))
        except Exception:
            sql = str(stmt)
        executed_sql.append(sql)
        for marker in fail_markers:
            if marker in sql:
                raise RuntimeError(f"simulated db failure on {marker}")
        for marker, result in handlers:
            if marker in sql:
                return result
        return _RowsResult([])

    @asynccontextmanager
    async def _begin_nested():
        state.nested_calls += 1
        yield

    db = SimpleNamespace(
        execute=AsyncMock(side_effect=_execute),
        begin_nested=_begin_nested,
    )
    return db, executed_sql, state


@pytest.mark.asyncio
async def test_sentence_query_uses_fts_and_semantic_layers():
    tenant_id = uuid4()
    playbook_id = uuid4()
    episode_near = uuid4()
    episode_far = uuid4()

    db, executed_sql, state = _dispatching_db(
        [
            ("search_tsvector", _RowsResult([(playbook_id, 0.7)])),
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
    assert ("playbook", "query_fts") in reasons
    seeded_ids = {s.ref.id for s in seeds}
    assert episode_near in seeded_ids
    assert episode_far not in seeded_ids  # similarity 0.3 < 0.5 floor
    # The semantic SQL ran inside a savepoint.
    assert state.nested_calls == 1
    # FTS is OR-composed keywords, never AND over the whole window.
    fts_sql = next(sql for sql in executed_sql if "websearch_to_tsquery" in sql)
    assert "plainto_tsquery" not in fts_sql
    # Only approved episodes are eligible for the ANN slots.
    episode_sql = next(sql for sql in executed_sql if "HALFVEC" in sql)
    assert "reviewer_state" in episode_sql


@pytest.mark.asyncio
async def test_semantic_sql_failure_does_not_poison_later_layers():
    """A DB-level failure in the ANN layer must stay inside its savepoint:
    Layer C and the rest of the projection keep working."""
    tenant_id = uuid4()
    entity_id = uuid4()

    db, executed_sql, state = _dispatching_db(
        [("external_id", _RowsResult([(entity_id,)]))],
        fail_markers=("HALFVEC",),
    )
    repo = SQLAlchemyAgentGraphRepository(db)

    with patch(
        "contextedge.ai.provider.generate_embedding",
        AsyncMock(return_value=[0.1] * 3072),
    ):
        seeds = await repo.resolve_seeds(
            AgentGraphRequest(query="MG22 did not deliver the output"),
            _scope(tenant_id),
        )

    assert state.nested_calls == 1
    # Layer C still ran and found the entity after the semantic failure.
    assert any(s.reason == "query_identifier_exact" for s in seeds)


@pytest.mark.asyncio
async def test_embedding_uses_newest_end_of_window():
    """The provider keeps the LAST 4k chars; the embedding must keep the
    last 2k of that — [:2000] would embed the oldest half."""
    tenant_id = uuid4()
    db, _, _ = _dispatching_db([])
    repo = SQLAlchemyAgentGraphRepository(db)

    query = ("old " * 700) + "the actual question about MG22"
    captured = {}

    async def _capture(text, **kwargs):
        captured["text"] = text
        raise RuntimeError("stop here")

    with patch("contextedge.ai.provider.generate_embedding", side_effect=_capture):
        await repo.resolve_seeds(AgentGraphRequest(query=query), _scope(tenant_id))

    normalized_query = " ".join(query.split())  # contract normalizes whitespace
    assert captured["text"] == normalized_query[-2_000:]
    assert "the actual question about MG22" in captured["text"]


@pytest.mark.asyncio
async def test_embedding_failure_is_soft_and_other_layers_survive():
    tenant_id = uuid4()
    session_id = uuid4()
    db, _, _ = _dispatching_db([])
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

    db, executed_sql, _ = _dispatching_db(
        [("external_id", _RowsResult([(entity_id,)]))]
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
async def test_exact_identity_lookup_is_tenant_prefixed():
    """The alias predicate must include identity_aliases.tenant_id or the
    0033 index's leading column is unbound (per-token seq scan)."""
    tenant_id = uuid4()
    db, executed_sql, _ = _dispatching_db([])
    repo = SQLAlchemyAgentGraphRepository(db)

    await repo._seed_entity_term(
        [], _scope(tenant_id), "jsmith@acme.com", reason="entity"
    )

    alias_sql = next(sql for sql in executed_sql if "normalized_alias" in sql)
    assert "identity_aliases.tenant_id" in alias_sql


@pytest.mark.asyncio
async def test_caps_word_tokens_never_run_substring_fallback():
    """Shouted words ('WHY IS THE VPN DOWN') may exact-match a real entity
    but must never seed arbitrary entities via ILIKE."""
    tenant_id = uuid4()
    db, executed_sql, _ = _dispatching_db([])
    repo = SQLAlchemyAgentGraphRepository(db)

    with patch(
        "contextedge.ai.provider.generate_embedding",
        AsyncMock(side_effect=RuntimeError("skip")),
    ):
        await repo.resolve_seeds(
            AgentGraphRequest(query="WHY IS THE VPN DOWN PLEASE HELP"),
            _scope(tenant_id),
        )

    assert not any("LIKE" in sql for sql in executed_sql)


@pytest.mark.asyncio
async def test_exact_entity_hit_suppresses_icontains_fallback():
    tenant_id = uuid4()
    entity_id = uuid4()
    db, executed_sql, _ = _dispatching_db(
        [("external_id", _RowsResult([(entity_id,)]))]
    )
    repo = SQLAlchemyAgentGraphRepository(db)

    seeds: list = []
    await repo._seed_entity_term(seeds, _scope(tenant_id), "MG22", reason="entity")

    assert [s.ref.id for s in seeds] == [entity_id]
    assert not any("LIKE" in sql.upper() and "entities" in sql for sql in executed_sql)


@pytest.mark.asyncio
async def test_seeds_are_deduplicated_capped_and_sorted():
    tenant_id = uuid4()
    dup = uuid4()
    db, _, _ = _dispatching_db([])
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
    assert seeds == sorted(
        seeds, key=lambda s: (-s.relevance, s.ref.type, str(s.ref.id))
    )


@pytest.mark.asyncio
async def test_semantic_layer_seeds_playbooks_directly():
    """0035: an embedded, approved playbook is seeded by meaning even when
    no episode history exists and its title shares no words with the query."""
    tenant_id = uuid4()
    playbook_near = uuid4()
    playbook_far = uuid4()

    db, executed_sql, state = _dispatching_db(
        [
            # Episode ANN → nothing (cold-start tenant)
            ("episodes", _RowsResult([])),
            # FTS finds nothing (title shares no words with the query) —
            # matched first because the FTS SQL contains search_tsvector.
            ("search_tsvector", _RowsResult([])),
            # Playbook ANN → one near hit, one unrelated
            ("playbooks", _RowsResult([(playbook_near, 0.25), (playbook_far, 0.8)])),
        ]
    )
    repo = SQLAlchemyAgentGraphRepository(db)

    with patch(
        "contextedge.ai.provider.generate_embedding",
        AsyncMock(return_value=[0.1] * 3072),
    ):
        seeds = await repo.resolve_seeds(
            AgentGraphRequest(query="users cannot log in anywhere this morning"),
            _scope(tenant_id),
        )

    semantic = [s for s in seeds if s.reason == "query_semantic"]
    seeded = {s.ref.id for s in semantic}
    assert playbook_near in seeded
    assert playbook_far not in seeded  # similarity 0.2 < 0.5 floor
    # The playbook ANN SQL is approved-only and embedding-not-null.
    pb_sql = next(s for s in executed_sql if "playbooks" in s and "HALFVEC" in s)
    assert "lifecycle_state" in pb_sql
    assert "embedding IS NOT NULL" in pb_sql
