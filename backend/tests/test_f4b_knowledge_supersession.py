"""F4b — retrieval can see that an article was replaced.

`services/documents/versioning.py` has always known that "VPN SOP v2.docx"
supersedes "VPN SOP.docx". Its own docstring names the gap it does not close:
retrieval "returns superseded guidance and nothing marks it as superseded".

The heuristic proposes; a human decides. Most of these tests are about what the
heuristic must NOT propose, because a filename is not grounds for retiring an
SOP and a wrong call silently removes the only guidance that exists.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from contextedge.models.knowledge_supersession import (
    SUPERSESSION_STATUSES,
    KnowledgeSupersessionProposal,
)
from contextedge.services.knowledge_supersession_service import (
    MIN_PROPOSAL_CONFIDENCE,
    compare_candidates,
    decide_proposal,
    propose_supersessions,
)


def _evidence(title, evidence_id=None):
    return SimpleNamespace(
        id=evidence_id or uuid.uuid4(), title=title, domain_id=None
    )


# =========================================================================
# What the heuristic proposes — and refuses to
# =========================================================================


def test_an_explicit_version_bump_is_the_strongest_signal():
    scored = compare_candidates("VPN SOP v1.docx", "VPN SOP v2.docx")
    assert scored is not None
    confidence, signals = scored
    assert confidence >= 0.9
    assert signals["basis"] == "explicit_version"


def test_a_reversed_pair_is_never_proposed():
    """Proposing a backwards supersession is worse than proposing nothing —
    accepting it would demote the current document in favour of the old one."""
    assert compare_candidates("VPN SOP v2.docx", "VPN SOP v1.docx") is None


def test_equal_versions_are_not_a_supersession():
    assert compare_candidates("VPN SOP v2.docx", "VPN SOP v2.docx") is None


def test_a_version_appearing_is_weaker_than_a_bump():
    """"v2" replacing an unversioned original is the common real case, but the
    unversioned file might be the newer rewrite."""
    bumped = compare_candidates("SOP v1.docx", "SOP v2.docx")
    added = compare_candidates("SOP.docx", "SOP v2.docx")
    assert added is not None and bumped is not None
    assert added[0] < bumped[0]
    assert added[1]["basis"] == "version_added"


def test_a_version_disappearing_is_not_a_supersession():
    """An unversioned file does not supersede a versioned one just by being
    unversioned — that is exactly how "SOP.docx" would bury "SOP v3.docx"."""
    assert compare_candidates("SOP v3.docx", "SOP.docx") is None


def test_revision_words_are_the_weakest_signal_and_must_disagree():
    scored = compare_candidates("VPN SOP draft.docx", "VPN SOP final.docx")
    assert scored is not None
    assert scored[1]["basis"] == "qualifier_words"
    assert scored[0] >= MIN_PROPOSAL_CONFIDENCE
    # Two documents with equally neutral names are not evidence of anything.
    assert compare_candidates("VPN SOP alpha.docx", "VPN SOP beta.docx") is None


def test_the_signals_travel_with_the_proposal():
    """A reviewer who cannot see WHY two documents were paired will either
    rubber-stamp it or ignore it."""
    _, signals = compare_candidates("SOP v1.docx", "SOP v2.docx")
    assert signals["predecessor"]["filename"] == "SOP v1.docx"
    assert signals["successor"]["version"] == (2,)


# =========================================================================
# Proposing over a corpus
# =========================================================================


class _NoExisting:
    def scalar_one_or_none(self):
        return None


def _db(existing=None):
    added: list = []
    db = SimpleNamespace(
        add=added.append,
        flush=AsyncMock(),
        execute=AsyncMock(return_value=existing or _NoExisting()),
        get=AsyncMock(return_value=None),
    )
    db.added = added
    return db


@pytest.mark.asyncio
async def test_only_documents_in_the_same_family_are_compared():
    """"VPN SOP v2" does not supersede "Printer SOP v1" just because both are
    v-something."""
    db = _db()
    proposals = await propose_supersessions(
        db,
        uuid.uuid4(),
        knowledge_evidence=[
            _evidence("VPN SOP v1.docx"),
            _evidence("VPN SOP v2.docx"),
            _evidence("Printer SOP v1.docx"),
        ],
    )
    assert len(proposals) == 1
    assert proposals[0].document_family == "vpn sop"


@pytest.mark.asyncio
async def test_a_lone_document_proposes_nothing():
    db = _db()
    assert await propose_supersessions(
        db, uuid.uuid4(), knowledge_evidence=[_evidence("VPN SOP v1.docx")]
    ) == []


@pytest.mark.asyncio
async def test_an_untitled_document_is_skipped_rather_than_guessed_at():
    db = _db()
    assert await propose_supersessions(
        db,
        uuid.uuid4(),
        knowledge_evidence=[_evidence(None), _evidence("   ")],
    ) == []


@pytest.mark.asyncio
async def test_an_already_decided_pair_is_never_re_proposed():
    """Rejection has to be durable — without it, a scheduled pass re-raises
    every declined pair forever and the queue becomes noise nobody reads."""

    class _Existing:
        def scalar_one_or_none(self):
            return KnowledgeSupersessionProposal(
                tenant_id=uuid.uuid4(),
                predecessor_evidence_id=uuid.uuid4(),
                successor_evidence_id=uuid.uuid4(),
                document_family="vpn sop",
                status="rejected",
            )

    db = _db(existing=_Existing())
    proposals = await propose_supersessions(
        db,
        uuid.uuid4(),
        knowledge_evidence=[_evidence("VPN SOP v1.docx"), _evidence("VPN SOP v2.docx")],
    )
    assert proposals == []


# =========================================================================
# Deciding
# =========================================================================


@pytest.mark.asyncio
async def test_accepting_writes_the_superseded_by_edge():
    proposal = KnowledgeSupersessionProposal(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        predecessor_evidence_id=uuid.uuid4(),
        successor_evidence_id=uuid.uuid4(),
        document_family="vpn sop",
        confidence=0.9,
        status="pending",
    )
    db = SimpleNamespace(get=AsyncMock(return_value=proposal), flush=AsyncMock())
    with patch(
        "contextedge.services.knowledge_supersession_service.ensure_edge", AsyncMock()
    ) as edge:
        result = await decide_proposal(
            db, proposal.tenant_id, proposal_id=proposal.id, accept=True,
            decided_by=uuid.uuid4(),
        )
    assert result.status == "accepted"
    assert edge.await_count == 1
    assert edge.await_args.args[6] == "superseded_by"


@pytest.mark.asyncio
async def test_rejecting_writes_no_edge():
    proposal = KnowledgeSupersessionProposal(
        id=uuid.uuid4(), tenant_id=uuid.uuid4(),
        predecessor_evidence_id=uuid.uuid4(), successor_evidence_id=uuid.uuid4(),
        document_family="vpn sop", status="pending",
    )
    db = SimpleNamespace(get=AsyncMock(return_value=proposal), flush=AsyncMock())
    with patch(
        "contextedge.services.knowledge_supersession_service.ensure_edge", AsyncMock()
    ) as edge:
        result = await decide_proposal(
            db, proposal.tenant_id, proposal_id=proposal.id, accept=False,
            decided_by=uuid.uuid4(),
        )
    assert result.status == "rejected"
    edge.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_decided_proposal_is_not_re_decided():
    """Flipping an accepted supersession would leave the edge behind, and a
    stale edge is worse than refusing the second decision."""
    proposal = KnowledgeSupersessionProposal(
        id=uuid.uuid4(), tenant_id=uuid.uuid4(),
        predecessor_evidence_id=uuid.uuid4(), successor_evidence_id=uuid.uuid4(),
        document_family="vpn sop", status="accepted",
    )
    db = SimpleNamespace(get=AsyncMock(return_value=proposal), flush=AsyncMock())
    with patch(
        "contextedge.services.knowledge_supersession_service.ensure_edge", AsyncMock()
    ) as edge:
        result = await decide_proposal(
            db, proposal.tenant_id, proposal_id=proposal.id, accept=False,
            decided_by=uuid.uuid4(),
        )
    assert result.status == "accepted"
    edge.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_foreign_tenants_proposal_is_not_decidable():
    proposal = KnowledgeSupersessionProposal(
        id=uuid.uuid4(), tenant_id=uuid.uuid4(),
        predecessor_evidence_id=uuid.uuid4(), successor_evidence_id=uuid.uuid4(),
        document_family="x", status="pending",
    )
    db = SimpleNamespace(get=AsyncMock(return_value=proposal), flush=AsyncMock())
    assert await decide_proposal(
        db, uuid.uuid4(), proposal_id=proposal.id, accept=True, decided_by=uuid.uuid4()
    ) is None


# =========================================================================
# Retrieval
# =========================================================================


def test_a_superseded_article_is_demoted_not_dropped():
    """When the successor does not match the query, the predecessor is still
    the only guidance that exists — hiding it leaves the reviewer with
    nothing and no indication anything was withheld."""
    from contextedge.services.knowledge_retrieval_service import (
        SUPERSEDED_RANK_FACTOR,
        KnowledgeDocument,
    )

    assert SUPERSEDED_RANK_FACTOR > 1.0
    doc = KnowledgeDocument(
        evidence_id=uuid.uuid4(),
        title="VPN SOP v1",
        evidence_type="sop",
        best_distance=0.10 * SUPERSEDED_RANK_FACTOR,
        superseded=True,
    )
    block = doc.to_prompt_block(1)
    assert "SUPERSEDED" in block
    assert "VPN SOP v1" in block


def test_supersession_outweighs_a_contested_run_record():
    """"This has been replaced" is a stronger statement about an article than
    "its run record is mixed" — and a human reviewed it, rather than it being
    a statistic."""
    from contextedge.services.knowledge_retrieval_service import (
        SUPERSEDED_RANK_FACTOR,
        SUPPORT_RANK_FACTORS,
    )

    assert SUPERSEDED_RANK_FACTOR > SUPPORT_RANK_FACTORS["contested"]


def test_the_successor_outranks_its_predecessor_for_the_same_query():
    """The acceptance criterion: same query, same similarity, one replaced."""
    from contextedge.services.knowledge_retrieval_service import SUPERSEDED_RANK_FACTOR

    successor = 0.12
    predecessor = 0.12 * SUPERSEDED_RANK_FACTOR
    assert successor < predecessor


def test_the_status_vocabulary_is_three_answers():
    assert set(SUPERSESSION_STATUSES) == {"pending", "accepted", "rejected"}


# =========================================================================
# The reviewer surface
#
# A proposal table with nowhere to review it is the same gap in new clothes:
# findings pile up, nobody sees them, retrieval keeps serving the replaced
# article.
# =========================================================================


def _api():
    from contextedge.api.v1 import knowledge_supersessions

    return knowledge_supersessions


class _Rows:
    def __init__(self, values):
        self._values = values

    def scalars(self):
        return SimpleNamespace(all=lambda: self._values)


@pytest.mark.asyncio
async def test_reviewing_supersessions_needs_the_knowledge_role():
    from fastapi import HTTPException

    from .conftest import make_user

    with pytest.raises(HTTPException) as excinfo:
        await _api().list_supersession_proposals(
            db=SimpleNamespace(), user=make_user(roles=["viewer"])
        )
    assert excinfo.value.status_code == 403


@pytest.mark.asyncio
async def test_the_queue_is_scoped_to_the_callers_tenant():
    from .conftest import make_user

    user = make_user(roles=["knowledge_manager"])
    captured = {}

    async def _execute(stmt):
        captured["sql"] = str(stmt)
        return _Rows([])

    await _api().list_supersession_proposals(
        db=SimpleNamespace(execute=_execute),
        user=user,
        status_filter="pending",
        limit=50,
        offset=0,
    )
    assert "knowledge_supersession_proposals.tenant_id = " in captured["sql"]
    assert "knowledge_supersession_proposals.status = " in captured["sql"]
    # Strongest signal first — an explicit v1→v2 bump before a draft/final pair.
    assert "confidence DESC" in captured["sql"]


@pytest.mark.asyncio
async def test_an_unknown_status_filter_is_refused_not_silently_empty():
    """An empty queue and a typo look identical to a reviewer."""
    from fastapi import HTTPException

    from .conftest import make_user

    with pytest.raises(HTTPException) as excinfo:
        await _api().list_supersession_proposals(
            db=SimpleNamespace(),
            user=make_user(roles=["knowledge_manager"]),
            status_filter="approved",
        )
    assert excinfo.value.status_code == 400


@pytest.mark.asyncio
async def test_reversing_someone_elses_decision_is_a_conflict_not_a_quiet_200():
    from fastapi import HTTPException

    from .conftest import make_user

    user = make_user(roles=["knowledge_manager"])
    already = KnowledgeSupersessionProposal(
        id=uuid.uuid4(), tenant_id=user.tenant_id,
        predecessor_evidence_id=uuid.uuid4(), successor_evidence_id=uuid.uuid4(),
        document_family="vpn sop", status="accepted",
    )
    api = _api()
    with patch.object(
        api, "decide_proposal", AsyncMock(return_value=already)
    ), pytest.raises(HTTPException) as excinfo:
        await api.decide_supersession_proposal(
            proposal_id=already.id,
            body=api.SupersessionDecision(accept=False),
            db=SimpleNamespace(),
            user=user,
        )
    assert excinfo.value.status_code == 409


@pytest.mark.asyncio
async def test_a_missing_proposal_is_a_404():
    from fastapi import HTTPException

    from .conftest import make_user

    api = _api()
    with patch.object(
        api, "decide_proposal", AsyncMock(return_value=None)
    ), pytest.raises(HTTPException) as excinfo:
        await api.decide_supersession_proposal(
            proposal_id=uuid.uuid4(),
            body=api.SupersessionDecision(accept=True),
            db=SimpleNamespace(),
            user=make_user(roles=["knowledge_manager"]),
        )
    assert excinfo.value.status_code == 404


def test_a_new_proposal_can_be_serialised_without_a_second_query():
    """The scan returns the rows it just created. A column with only a SERVER
    default is expired after an async flush, so reading it emits IO from
    attribute access — `MissingGreenlet`, in the response serialiser."""
    table = KnowledgeSupersessionProposal.__table__
    for column in ("id", "created_at", "signals", "status", "confidence"):
        assert table.c[column].default is not None, column


@pytest.mark.asyncio
async def test_a_scan_only_reads_knowledge_evidence():
    """A ticket titled "VPN issue v2" is not a replacement for an SOP."""
    from contextedge.services.evidence_typing import KNOWLEDGE_EVIDENCE_TYPES
    from contextedge.services.knowledge_supersession_service import (
        MAX_SCANNED_DOCUMENTS,
        scan_tenant_knowledge,
    )

    captured = {}

    async def _execute(stmt):
        captured.setdefault("sql", str(stmt))
        return _Rows([])

    db = SimpleNamespace(execute=_execute, add=lambda row: None, flush=AsyncMock())
    assert await scan_tenant_knowledge(db, uuid.uuid4()) == []
    assert "evidence_type IN" in captured["sql"]
    # Bounded, and newest-first so the bound keeps the end that matters: a
    # document that replaced something is likelier to be the recent one.
    assert "LIMIT" in captured["sql"]
    assert "coalesce" in captured["sql"] and "DESC" in captured["sql"]
    assert MAX_SCANNED_DOCUMENTS > 0
    assert KNOWLEDGE_EVIDENCE_TYPES
