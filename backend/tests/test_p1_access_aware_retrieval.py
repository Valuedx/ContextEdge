from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from contextedge.search.hybrid_ranker import rank_playbooks
from contextedge.search.playbook_candidates import CandidateSet
from contextedge.services.playbook_applicability import ApplicabilityVerdict
from contextedge.search.pg_fts import search_evidence_fts
from contextedge.search.vector_search import search_evidence_semantic


class _AllResult:
    def __init__(self, values):
        self._values = values

    def all(self):
        return self._values


class _ScalarsResult:
    def __init__(self, values):
        self._values = values

    def scalars(self):
        return SimpleNamespace(all=lambda: self._values)


@pytest.mark.asyncio
async def test_search_evidence_semantic_applies_policy_exclusion():
    tenant_id = uuid4()
    policy_id = uuid4()
    captured = {}

    async def execute(stmt):
        captured["sql"] = str(stmt.compile(dialect=postgresql.dialect()))
        return _AllResult([])

    db = SimpleNamespace(execute=AsyncMock(side_effect=execute))

    await search_evidence_semantic(
        db,
        tenant_id,
        "database timeout",
        query_embedding=[0.1, 0.2],
        exclude_policy_ids=[policy_id],
    )

    assert "access_policy_id" in captured["sql"]
    assert "NOT IN" in captured["sql"]


@pytest.mark.asyncio
async def test_search_evidence_fts_applies_policy_exclusion():
    tenant_id = uuid4()
    policy_id = uuid4()
    captured = {}

    async def execute(stmt):
        captured["sql"] = str(stmt.compile(dialect=postgresql.dialect()))
        return _AllResult([])

    db = SimpleNamespace(execute=AsyncMock(side_effect=execute))

    await search_evidence_fts(
        db,
        tenant_id,
        "foo",
        exclude_policy_ids=[policy_id],
    )

    assert "access_policy_id" in captured["sql"]
    assert "NOT IN" in captured["sql"]


@pytest.mark.asyncio
async def test_rank_playbooks_uses_candidate_union_not_per_playbook_evidence():
    """G5.4: ranking scores a candidate union; it does not loop evidence ANN."""
    tenant_id = uuid4()
    playbook_id = uuid4()
    playbook = SimpleNamespace(
        id=playbook_id,
        tenant_id=tenant_id,
        lifecycle_state="approved",
        domain_id=None,
        risk_tier="medium",
        expiry_at=None,
        last_validated_at=datetime.now(timezone.utc),
    )
    version = SimpleNamespace(
        id=uuid4(),
        playbook_confidence=0.8,
        trigger_conditions={},
        conflicts=None,
        semantic_version="1.0.0",
    )
    candidates = CandidateSet(
        playbooks={playbook_id: playbook},
        arm_ranks={"r1_embedding": [playbook_id]},
    )
    db = SimpleNamespace(execute=AsyncMock())
    generate_mock = AsyncMock(return_value=candidates)

    with (
        patch(
            "contextedge.search.hybrid_ranker.generate_playbook_candidates",
            generate_mock,
        ),
        patch(
            "contextedge.search.hybrid_ranker._latest_published_versions",
            AsyncMock(return_value={playbook_id: version}),
        ),
        patch(
            "contextedge.search.hybrid_ranker.resolve_identity_ids_for_terms",
            AsyncMock(return_value=set()),
        ),
        patch(
            "contextedge.search.hybrid_ranker._batch_graph_counts",
            AsyncMock(return_value={playbook_id: 0}),
        ),
        patch(
            "contextedge.search.hybrid_ranker._batch_identity_hits",
            AsyncMock(return_value={playbook_id: 0}),
        ),
        patch(
            "contextedge.search.hybrid_ranker._batch_contradiction_counts",
            AsyncMock(return_value={playbook_id: 0}),
        ),
        patch(
            "contextedge.search.hybrid_ranker._batch_precedent_counts",
            AsyncMock(return_value={playbook_id: 0}),
        ),
        patch(
            "contextedge.search.hybrid_ranker._batch_evidence_link_counts",
            AsyncMock(return_value={playbook_id: 0}),
        ),
        patch(
            "contextedge.search.hybrid_ranker.evaluate_trigger_conditions",
            lambda *a, **k: ApplicabilityVerdict(level="unvalidated"),
        ),
    ):
        ranked = await rank_playbooks(
            db,
            tenant_id=tenant_id,
            query_text="restart database",
            caller_roles=["knowledge_manager"],
            min_score=0.0,
        )

    assert len(ranked) == 1
    assert ranked[0].playbook_version_id == version.id
    assert generate_mock.await_args.kwargs["caller_roles"] == ["knowledge_manager"]


@pytest.mark.asyncio
async def test_rank_playbooks_admin_sees_all_evidence():
    tenant_id = uuid4()
    playbook_id = uuid4()
    playbook = SimpleNamespace(
        id=playbook_id,
        tenant_id=tenant_id,
        lifecycle_state="approved",
        domain_id=None,
        risk_tier="medium",
        expiry_at=None,
        last_validated_at=datetime.now(timezone.utc),
    )
    version = SimpleNamespace(
        id=uuid4(),
        playbook_confidence=0.8,
        trigger_conditions={},
        conflicts=None,
        semantic_version="1.0.0",
    )
    candidates = CandidateSet(
        playbooks={playbook_id: playbook},
        arm_ranks={"r2_lexical": [playbook_id]},
    )
    db = SimpleNamespace(execute=AsyncMock())

    with (
        patch(
            "contextedge.search.hybrid_ranker.generate_playbook_candidates",
            AsyncMock(return_value=candidates),
        ),
        patch(
            "contextedge.search.hybrid_ranker._latest_published_versions",
            AsyncMock(return_value={playbook_id: version}),
        ),
        patch(
            "contextedge.search.hybrid_ranker.resolve_identity_ids_for_terms",
            AsyncMock(return_value=set()),
        ),
        patch(
            "contextedge.search.hybrid_ranker._batch_graph_counts",
            AsyncMock(return_value={playbook_id: 0}),
        ),
        patch(
            "contextedge.search.hybrid_ranker._batch_identity_hits",
            AsyncMock(return_value={playbook_id: 0}),
        ),
        patch(
            "contextedge.search.hybrid_ranker._batch_contradiction_counts",
            AsyncMock(return_value={playbook_id: 0}),
        ),
        patch(
            "contextedge.search.hybrid_ranker._batch_precedent_counts",
            AsyncMock(return_value={playbook_id: 0}),
        ),
        patch(
            "contextedge.search.hybrid_ranker._batch_evidence_link_counts",
            AsyncMock(return_value={playbook_id: 0}),
        ),
        patch(
            "contextedge.search.hybrid_ranker.evaluate_trigger_conditions",
            lambda *a, **k: ApplicabilityVerdict(level="exact"),
        ),
    ):
        ranked = await rank_playbooks(
            db,
            tenant_id=tenant_id,
            query_text="restart database",
            caller_roles=["domain_admin"],
            min_score=0.0,
        )

    assert len(ranked) == 1
    assert ranked[0].applicability == "exact"
