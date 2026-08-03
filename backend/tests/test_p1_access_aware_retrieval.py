from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from contextedge.search.hybrid_ranker import rank_playbooks
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
async def test_rank_playbooks_passes_excluded_policies_for_non_admin():
    tenant_id = uuid4()
    restricted_policy_id = uuid4()
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
    version = SimpleNamespace(id=uuid4(), playbook_confidence=0.8)
    db = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                _ScalarsResult([SimpleNamespace(id=restricted_policy_id, config={"restricted": True})]),
                _ScalarsResult([playbook]),
            ]
        ),
        get=AsyncMock(return_value=version),
    )

    with (
        patch(
            "contextedge.search.hybrid_ranker._latest_published_versions",
            AsyncMock(return_value={playbook.id: version}),
        ),
        patch("contextedge.search.hybrid_ranker._graph_score_for_playbook", AsyncMock(return_value=0.0)),
        patch("contextedge.search.hybrid_ranker._negative_penalty_for_playbook", AsyncMock(return_value=0.0)),
        patch("contextedge.search.hybrid_ranker.resolve_identity_ids_for_terms", AsyncMock(return_value=set())),
        patch("contextedge.search.hybrid_ranker.search_playbooks_fts", AsyncMock(return_value=[])),
        patch("contextedge.search.hybrid_ranker.generate_embedding", AsyncMock(return_value=[0.1, 0.2])),
        patch(
            "contextedge.search.hybrid_ranker.search_evidence_semantic_for_playbook",
            AsyncMock(return_value=[]),
        ) as semantic_mock,
    ):
        await rank_playbooks(
            db,
            tenant_id=tenant_id,
            query_text="restart database",
            caller_roles=["knowledge_manager"],
        )

    assert semantic_mock.await_args.kwargs["exclude_policy_ids"] == [restricted_policy_id]


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
    version = SimpleNamespace(id=uuid4(), playbook_confidence=0.8)
    db = SimpleNamespace(
        execute=AsyncMock(return_value=_ScalarsResult([playbook])),
        get=AsyncMock(return_value=version),
    )

    with (
        patch(
            "contextedge.search.hybrid_ranker._latest_published_versions",
            AsyncMock(return_value={playbook.id: version}),
        ),
        patch("contextedge.search.hybrid_ranker._graph_score_for_playbook", AsyncMock(return_value=0.0)),
        patch("contextedge.search.hybrid_ranker._negative_penalty_for_playbook", AsyncMock(return_value=0.0)),
        patch("contextedge.search.hybrid_ranker.resolve_identity_ids_for_terms", AsyncMock(return_value=set())),
        patch("contextedge.search.hybrid_ranker.search_playbooks_fts", AsyncMock(return_value=[])),
        patch("contextedge.search.hybrid_ranker.generate_embedding", AsyncMock(return_value=[0.1, 0.2])),
        patch(
            "contextedge.search.hybrid_ranker.search_evidence_semantic_for_playbook",
            AsyncMock(return_value=[]),
        ) as semantic_mock,
    ):
        await rank_playbooks(
            db,
            tenant_id=tenant_id,
            query_text="restart database",
            caller_roles=["domain_admin"],
        )

    assert semantic_mock.await_args.kwargs["exclude_policy_ids"] is None
