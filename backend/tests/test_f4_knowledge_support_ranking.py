"""F4 — retrieval can finally see whether a procedure has ever worked.

`knowledge_validation_service` has computed empirical support from *verified*
execution outcomes since Phase 3. `knowledge_retrieval_service` ranked on
semantic distance and applicability alone, so a procedure with a documented
history of failure ranked exactly like one with a history of success.

These tests pin the ranking arithmetic, and — more importantly — the two
principles that decide what NOT to do with it: support re-ranks and never
filters, and silence is not failure.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from contextedge.services.knowledge_retrieval_service import (
    SUPPORT_RANK_FACTORS,
    KnowledgeDocument,
    support_rank_factor,
)


def test_proven_promotes_and_contested_demotes():
    assert SUPPORT_RANK_FACTORS["proven"] < 1.0
    assert SUPPORT_RANK_FACTORS["emerging"] < 1.0
    assert SUPPORT_RANK_FACTORS["contested"] > 1.0
    assert support_rank_factor({"support": "proven"})[0] == SUPPORT_RANK_FACTORS["proven"]
    assert support_rank_factor({"support": "contested"})[0] == SUPPORT_RANK_FACTORS["contested"]


def test_never_exercised_is_neutral_not_penalised():
    """Silence is not failure. Most knowledge is simply never exercised, and
    treating "no runs" as a negative signal would demote the whole corpus on
    day one — the principle knowledge_validation_service states, holding here
    where it has to hold to mean anything."""
    assert SUPPORT_RANK_FACTORS["unproven"] == 1.0
    assert support_rank_factor({"support": "unproven"}) == (1.0, "unproven")
    # An article that has never been computed is the same case.
    assert support_rank_factor(None) == (1.0, None)


def test_malformed_or_unknown_support_is_neutral():
    """A ranker that raised or guessed here would turn a data problem into a
    retrieval that silently returns the wrong articles."""
    for stored in (None, {}, [], "proven", {"support": 7}, {"support": "from_the_future"}):
        factor, _ = support_rank_factor(stored)
        assert factor == 1.0, stored


def test_a_contested_article_still_reaches_the_generator():
    """Support re-ranks; it never filters. A procedure with a failure history
    is often the only guidance that exists, and dropping it leaves the
    reviewer with nothing and no indication anything was withheld."""
    contested = KnowledgeDocument(
        evidence_id=uuid.uuid4(),
        title="VPN certificate renewal (disputed)",
        evidence_type="kb_article",
        best_distance=0.10 * SUPPORT_RANK_FACTORS["contested"],
        support="contested",
    )
    block = contested.to_prompt_block(1)
    assert "SUPPORT WARNING" in block
    assert "VPN certificate renewal" in block
    # Still inside the retrieval ceiling, so demotion does not silently drop it.
    from contextedge.services.knowledge_retrieval_service import MAX_DISTANCE

    assert contested.best_distance < MAX_DISTANCE


def test_the_demotion_is_mild_enough_to_be_a_reordering():
    """A contested article is not wrong; it is inconsistent. The penalty has
    to reorder without exiling — a clearly-better-matching contested article
    should still beat a barely-matching proven one."""
    close_contested = 0.05 * SUPPORT_RANK_FACTORS["contested"]
    distant_proven = 0.20 * SUPPORT_RANK_FACTORS["proven"]
    assert close_contested < distant_proven


def test_support_beats_a_tie_on_semantic_distance():
    """The case the whole item exists for: same query, same similarity, one
    procedure known to work and one known to fail."""
    proven = 0.12 * SUPPORT_RANK_FACTORS["proven"]
    contested = 0.12 * SUPPORT_RANK_FACTORS["contested"]
    unproven = 0.12 * SUPPORT_RANK_FACTORS["unproven"]
    assert proven < unproven < contested


# =========================================================================
# Persistence
# =========================================================================


@pytest.mark.asyncio
async def test_persist_stores_the_support_level_and_its_counts():
    from contextedge.services import knowledge_validation_service as kvs

    tenant_id, evidence_id = uuid.uuid4(), uuid.uuid4()
    evidence = SimpleNamespace(id=evidence_id, tenant_id=tenant_id, knowledge_support=None)
    db = SimpleNamespace(get=AsyncMock(return_value=evidence), flush=AsyncMock())

    validation = kvs.KnowledgeValidation(
        evidence_id=evidence_id,
        title="VPN certificate renewal",
        support=kvs.SUPPORT_PROVEN,
        executions=4,
        verified_successes=4,
    )
    with patch.object(kvs, "validate_knowledge_item", AsyncMock(return_value=validation)):
        result = await kvs.persist_knowledge_support(db, tenant_id, evidence_id)

    assert result is validation
    assert evidence.knowledge_support["support"] == "proven"
    # The counts ride along so a reviewer asking "why contested?" does not
    # have to re-run the query.
    assert evidence.knowledge_support["verified_successes"] == 4
    assert "title" not in evidence.knowledge_support


@pytest.mark.asyncio
async def test_persist_refuses_a_foreign_tenants_evidence():
    from contextedge.services import knowledge_validation_service as kvs

    evidence = SimpleNamespace(id=uuid.uuid4(), tenant_id=uuid.uuid4(), knowledge_support=None)
    db = SimpleNamespace(get=AsyncMock(return_value=evidence), flush=AsyncMock())
    assert await kvs.persist_knowledge_support(db, uuid.uuid4(), evidence.id) is None
    assert evidence.knowledge_support is None


@pytest.mark.asyncio
async def test_refresh_is_bounded_to_one_versions_citations():
    """Bounded by the citations of a single playbook version — a handful of
    articles, not the corpus. The verdict only changed those."""
    from contextedge.services import knowledge_validation_service as kvs

    tenant_id = uuid.uuid4()
    cited = [uuid.uuid4(), uuid.uuid4(), None]

    class _Result:
        def scalars(self):
            return SimpleNamespace(all=lambda: cited)

    db = SimpleNamespace(execute=AsyncMock(return_value=_Result()), flush=AsyncMock())
    calls: list = []

    async def _persist(_db, _tenant, evidence_id):
        calls.append(evidence_id)
        return object()

    with patch.object(kvs, "persist_knowledge_support", _persist):
        refreshed = await kvs.refresh_support_for_playbook_version(db, tenant_id, uuid.uuid4())

    assert refreshed == 2
    assert set(calls) == {c for c in cited if c}
