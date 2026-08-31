"""Phase 1: evidence typing, knowledge authority, and playbook provenance.

These cover repairs to paths that were already shipped but not running.
Each test names the silent failure it prevents, because every one of
these defects returned a plausible-looking empty or default value rather
than an error — which is why they survived.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from contextedge.services.evidence_typing import (
    derive_evidence_type,
    is_knowledge_evidence,
)
from contextedge.services.playbook_service import _materialize_evidence_links

# --- 1.1 evidence_type derivation -------------------------------------------


def _payload(source_type: str, object_type: str, **extra):
    return {
        "_connector_source_type": source_type,
        "_connector_object_type": object_type,
        **extra,
    }


def test_servicenow_kb_articles_are_typed_as_knowledge():
    """The headline defect: kb_knowledge landed as "message", so KB
    articles were indistinguishable from chat lines everywhere
    downstream."""
    assert derive_evidence_type(_payload("servicenow", "kb_knowledge")) == "kb_article"


@pytest.mark.parametrize(
    "object_type,expected",
    [
        ("incident", "incident"),
        ("problem", "problem"),
        ("change_request", "change"),
        ("sc_req_item", "service_request"),
        ("sc_task", "task"),
        ("em_alert", "alert"),
        ("em_alert_rollup", "alert"),
    ],
)
def test_servicenow_record_kinds_are_distinguished(object_type, expected):
    assert derive_evidence_type(_payload("servicenow", object_type)) == expected


@pytest.mark.parametrize(
    "source_type,object_type,expected",
    [
        ("jira_sm", "issue", "ticket"),
        ("sapphireims", "ticket", "ticket"),
        ("zoho_desk", "tickets", "ticket"),
        ("zoho_desk", "articles", "kb_article"),
        ("zoho_desk", "official_catalog", "documentation"),
        ("teams", "channel_message", "chat_message"),
        ("gmail", "email_thread", "email"),
    ],
)
def test_other_connectors_map_to_their_own_kinds(source_type, object_type, expected):
    assert derive_evidence_type(_payload(source_type, object_type)) == expected


def test_explicit_connector_evidence_type_wins():
    """A connector that distinguishes record shapes itself must not be
    overridden by the table — zoho_desk emits both tickets and articles
    from one source and says which is which."""
    payload = _payload("zoho_desk", "tickets", evidence_type="kb_article")
    assert derive_evidence_type(payload) == "kb_article"


def test_unknown_object_type_falls_back_to_the_source_default():
    """A new ServiceNow table should be a ticket, not a chat message.
    Wrong-but-adjacent beats wrong-and-misleading."""
    assert derive_evidence_type(_payload("servicenow", "u_custom_table")) == "ticket"
    assert derive_evidence_type(_payload("teams", "something_new")) == "chat_message"


def test_unknown_source_falls_back_to_message():
    assert derive_evidence_type(_payload("brand_new_system", "thing")) == "message"
    assert derive_evidence_type({}) == "message"
    assert derive_evidence_type(None) == "message"


def test_blank_explicit_type_does_not_win():
    """An empty string must not beat the derivation — it is absence, not
    an assertion."""
    payload = _payload("servicenow", "kb_knowledge", evidence_type="   ")
    assert derive_evidence_type(payload) == "kb_article"


def test_knowledge_predicate_matches_the_memory_layer():
    """is_knowledge_evidence must agree with KB_LONG_TERM_TYPES, or
    knowledge is authoritative in one subsystem and not the other."""
    from contextedge.services.memory_service import KB_LONG_TERM_TYPES

    for evidence_type in KB_LONG_TERM_TYPES:
        assert is_knowledge_evidence(evidence_type) is True
    assert is_knowledge_evidence("incident") is False
    assert is_knowledge_evidence(None) is False


def test_kb_articles_now_reach_long_term_memory():
    """Regression for the chain: KB_LONG_TERM_TYPES was a correct set
    that nothing produced members for, so knowledge was never promoted."""
    from contextedge.services.memory_service import (
        LONG_TERM_MEMORY,
        classify_evidence_memory_class,
    )

    derived = derive_evidence_type(_payload("servicenow", "kb_knowledge"))
    evidence = SimpleNamespace(evidence_type=derived, canonical_entity_refs=None)
    assert classify_evidence_memory_class(evidence) == LONG_TERM_MEMORY


# --- 1.2 chunk source authority ---------------------------------------------


def test_knowledge_articles_do_not_get_ticket_authority():
    """A KB article is normative, not a record of events. Stamping it
    "ticket" fed the reranker a lifecycle-authority claim the record
    does not have."""
    from contextedge.services.evidence_chunk_service import _default_authority

    assert _default_authority("servicenow", "kb_article") == "knowledge_article"
    assert _default_authority("servicenow", "sop") == "knowledge_article"
    # Same source, different record kind — the distinction that was missing.
    assert _default_authority("servicenow", "incident") == "ticket"


def test_authority_falls_back_to_source_when_type_is_unremarkable():
    """Existing behaviour for non-knowledge records is unchanged."""
    from contextedge.services.evidence_chunk_service import _default_authority

    assert _default_authority("jira_sm", "ticket") == "ticket"
    assert _default_authority("gmail", "email") == "email"
    assert _default_authority("teams", "chat_message") == "chat"
    assert _default_authority("local_file", "document") == "gist"
    # Called without an evidence type at all (legacy callers).
    assert _default_authority("servicenow") == "ticket"


def test_ticket_authority_now_covers_the_newer_ticket_sources():
    """sapphireims and zoho_desk were falling through to "gist"."""
    from contextedge.services.evidence_chunk_service import _default_authority

    assert _default_authority("sapphireims", "ticket") == "ticket"
    assert _default_authority("zoho_desk", "ticket") == "ticket"


# --- 1.1b synthesis role ----------------------------------------------------


def test_knowledge_carries_document_authority_in_synthesis():
    """Without this a general "how the VPN works" page competes with the
    incident record on incident-specific fields."""
    from contextedge.workers.extraction_tasks import resolve_synthesis_role

    assert resolve_synthesis_role("servicenow", None, "kb_article") == "document"
    assert resolve_synthesis_role("servicenow", None, "sop") == "document"
    assert resolve_synthesis_role("servicenow", None, "alert") == "monitoring"
    assert resolve_synthesis_role("servicenow", None, "incident") == "ticket"
    # Source default when the type says nothing special.
    assert resolve_synthesis_role("servicenow", None) == "ticket"
    assert resolve_synthesis_role("teams", {}) == "working_discussion"
    # An explicit source override still wins over both.
    assert (
        resolve_synthesis_role("servicenow", {"synthesis_role": "monitoring"}, "kb_article")
        == "monitoring"
    )


# --- 1.3 playbook evidence links --------------------------------------------


class _FakeSession:
    def __init__(self):
        self.added = []

    def add(self, obj):
        self.added.append(obj)


def test_evidence_refs_become_normalized_link_rows():
    """The defect this fixes: PlaybookEvidenceLink was read by two search
    paths and written by nothing, so playbook-scoped search inner-joined
    an empty table and returned zero rows every time."""
    db = _FakeSession()
    version = SimpleNamespace(id=uuid.uuid4(), tenant_id=uuid.uuid4())
    ev1, ev2, ep1 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    written = _materialize_evidence_links(
        db,
        version,
        {"evidence_ids": [str(ev1), str(ev2)], "episode_ids": [str(ep1)]},
    )

    assert written == 3
    assert len(db.added) == 3
    evidence_rows = [r for r in db.added if r.evidence_id is not None]
    episode_rows = [r for r in db.added if r.episode_id is not None]
    assert {r.evidence_id for r in evidence_rows} == {ev1, ev2}
    assert {r.episode_id for r in episode_rows} == {ep1}
    assert all(r.playbook_version_id == version.id for r in db.added)


def test_link_materialization_is_tolerant_of_model_authored_shapes():
    """evidence_refs is written by an LLM path; a malformed blob must not
    fail version creation — losing a provenance row is recoverable,
    failing the write is not."""
    db = _FakeSession()
    version = SimpleNamespace(id=uuid.uuid4(), tenant_id=uuid.uuid4())

    assert _materialize_evidence_links(db, version, None) == 0
    assert _materialize_evidence_links(db, version, {}) == 0
    assert _materialize_evidence_links(db, version, "junk") == 0
    assert _materialize_evidence_links(db, version, {"evidence_ids": "not-a-list"}) == 0
    # Non-UUID entries are skipped, valid ones still land.
    good = uuid.uuid4()
    assert (
        _materialize_evidence_links(
            db, version, {"evidence_ids": ["nonsense", str(good)]}
        )
        == 1
    )


def test_link_materialization_dedupes_and_accepts_a_bare_list():
    db = _FakeSession()
    version = SimpleNamespace(id=uuid.uuid4(), tenant_id=uuid.uuid4())
    ev = uuid.uuid4()
    assert _materialize_evidence_links(db, version, [str(ev), str(ev)]) == 1


def test_link_materialization_is_bounded():
    """A runaway generation must not write unbounded rows for one version."""
    from contextedge.services.playbook_service import MAX_EVIDENCE_LINKS

    db = _FakeSession()
    version = SimpleNamespace(id=uuid.uuid4(), tenant_id=uuid.uuid4())
    many = [str(uuid.uuid4()) for _ in range(MAX_EVIDENCE_LINKS + 50)]
    assert _materialize_evidence_links(db, version, {"evidence_ids": many}) == (
        MAX_EVIDENCE_LINKS
    )


# --- 1.4 pattern evidence resolution ----------------------------------------


@pytest.mark.asyncio
async def test_pattern_evidence_resolves_through_episode_links():
    """PatternEvidenceLink.evidence_id is never populated by
    create_pattern_from_episodes, so the generated playbook's
    evidence_refs was empty for every auto-generated pattern. Migration
    0037's episode_evidence_links is the maintained grounding."""
    from contextedge.workers.pattern_tasks import _evidence_ids_for_episodes

    tenant_id = uuid.uuid4()
    ep = uuid.uuid4()
    ev1, ev2 = uuid.uuid4(), uuid.uuid4()

    class _Result:
        def __init__(self, values):
            self._values = values

        def scalars(self):
            return SimpleNamespace(all=lambda: self._values)

    db = SimpleNamespace(execute=AsyncMock(return_value=_Result([ev1, ev2])))
    out = await _evidence_ids_for_episodes(db, tenant_id, [ep])

    assert out == sorted({str(ev1), str(ev2)})


@pytest.mark.asyncio
async def test_pattern_evidence_falls_back_to_legacy_episode_blob():
    """Episodes written before 0037 have no link rows but real grounding
    — dropping them would silently narrow provenance."""
    from contextedge.workers.pattern_tasks import _evidence_ids_for_episodes

    ev = uuid.uuid4()
    calls = []

    class _Result:
        def __init__(self, values):
            self._values = values

        def scalars(self):
            return SimpleNamespace(all=lambda: self._values)

    async def execute(stmt):
        calls.append(stmt)
        # First call: no normalized links. Second: the legacy JSONB blob.
        return _Result([]) if len(calls) == 1 else _Result([[str(ev)]])

    db = SimpleNamespace(execute=execute)
    out = await _evidence_ids_for_episodes(db, uuid.uuid4(), [uuid.uuid4()])

    assert out == [str(ev)]
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_pattern_evidence_short_circuits_on_no_episodes():
    from contextedge.workers.pattern_tasks import _evidence_ids_for_episodes

    db = SimpleNamespace(execute=AsyncMock())
    assert await _evidence_ids_for_episodes(db, uuid.uuid4(), []) == []
    assert db.execute.await_count == 0


# --- 1.5 the silent-empty guard ---------------------------------------------


@pytest.mark.asyncio
async def test_playbook_scoped_search_logs_when_the_version_has_no_links():
    """An empty result from a playbook-scoped search is ambiguous: "no
    match" vs "no provenance rows exist". For a long time it was always
    the second, silently. Say which."""
    from contextedge.search import vector_search

    version_id = uuid.uuid4()

    class _Rows:
        def all(self):
            return []

    class _Count:
        def scalar_one(self):
            return 0

    calls = {"n": 0}

    async def execute(stmt):
        calls["n"] += 1
        return _Rows() if calls["n"] == 1 else _Count()

    db = SimpleNamespace(execute=execute)

    with patch.object(vector_search.logger, "warning") as warn:
        out = await vector_search._chunk_candidates(
            db,
            uuid.uuid4(),
            [0.0] * 3072,
            exclude_policy_ids=None,
            limit=5,
            playbook_id=uuid.uuid4(),
            playbook_version_id=version_id,
        )

    assert out == []
    assert warn.called
    assert warn.call_args.args[0] == "search.playbook_scope_has_no_evidence_links"


# --- backfill task wrapper ---------------------------------------------------


def test_backfill_task_uses_the_shared_session_runner():
    """Covers the wrapper, not just the derivation.

    Two bugs shipped here because every test called `_backfill` directly
    and none invoked the task: it passed a coroutine to `run_async`
    (which wants a callable) and read `raw.payload` instead of
    `raw.raw_payload`. Both failed on the first real invocation.
    """
    from unittest.mock import patch as _patch

    from contextedge.workers import evidence_typing_tasks as mod

    captured = {}

    def fake_run_async(fn):
        captured["callable"] = callable(fn)
        return {"scanned": 0, "updated": 0}

    with _patch.object(mod, "run_async", side_effect=fake_run_async):
        result = mod.backfill_evidence_types.apply(kwargs={"tenant_id": "all"})

    assert result.state == "SUCCESS"
    assert captured["callable"] is True


@pytest.mark.asyncio
async def test_backfill_reads_raw_payload_and_skips_offloaded():
    """An offloaded row stores {"_offloaded": true} inline. That stub is
    a dict, so deriving from it returns "message" — which would OVERWRITE
    a correct type with a wrong one, the opposite of the repair."""
    from contextedge.workers.evidence_typing_tasks import _backfill

    inline = SimpleNamespace(
        evidence_type="message",
        raw_object_ref=uuid.uuid4(),
    )
    inline_raw = SimpleNamespace(
        raw_payload={
            "_connector_source_type": "servicenow",
            "_connector_object_type": "kb_knowledge",
        }
    )
    offloaded = SimpleNamespace(evidence_type="incident", raw_object_ref=uuid.uuid4())
    offloaded_raw = SimpleNamespace(raw_payload={"_offloaded": True})

    class _Rows:
        def __init__(self, rows):
            self._rows = rows

        def all(self):
            return self._rows

        def scalars(self):
            return SimpleNamespace(all=lambda: [])

    calls = {"n": 0}

    async def execute(stmt):
        calls["n"] += 1
        if calls["n"] == 1:  # tenant list
            return _Rows([(uuid.uuid4(),)])
        return _Rows([(inline, inline_raw), (offloaded, offloaded_raw)])

    db = SimpleNamespace(execute=execute, flush=AsyncMock())
    totals = await _backfill(db, "all", 100)

    assert inline.evidence_type == "kb_article"
    # Untouched: the offloaded stub must not be derived from.
    assert offloaded.evidence_type == "incident"
    assert totals["updated"] == 1
    assert totals["skipped_no_raw"] == 1
