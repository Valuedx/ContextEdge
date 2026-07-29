from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from contextedge.models.episode import EvidenceIdentityLink
from contextedge.models.episode import Episode
from contextedge.models.episode import CorrelationEdge
from contextedge.models.evidence import EvidenceItem
from contextedge.search.hybrid_ranker import rank_playbooks
from contextedge.services.correlation_service import correlate_evidence_item
from contextedge.services.episode_service import create_episodes_from_evidence
from contextedge.services.identity_service import link_evidence_identities


class _ScalarsResult:
    def __init__(self, values):
        self._values = values

    def scalars(self):
        return SimpleNamespace(all=lambda: self._values)


class _AllResult:
    def __init__(self, values):
        self._values = values

    def all(self):
        return self._values


class _ScalarOneOrNoneResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


@pytest.mark.asyncio
async def test_link_evidence_identities_updates_refs_and_links():
    tenant_id = uuid4()
    evidence_id = uuid4()
    identity_id = uuid4()
    # Service now passes evidence_item=<this> via ORM relationship; a
    # SimpleNamespace lacks _sa_instance_state. Use a real EvidenceItem
    # instance — no DB session required for in-memory construction.
    evidence = EvidenceItem(
        id=evidence_id,
        tenant_id=tenant_id,
        source_id=uuid4(),
        evidence_type="message",
        canonical_entity_refs=None,
    )
    added = []
    db = SimpleNamespace(
        execute=AsyncMock(return_value=_ScalarsResult([])),
        add=lambda obj: added.append(obj),
        flush=AsyncMock(),
    )

    with (
        patch(
            "contextedge.services.identity_service.resolve_entities_from_text",
            AsyncMock(
                return_value=[
                    {
                        "canonical_id": identity_id,
                        "canonical_name": "srv-db-01",
                        "entity_type": "device",
                        "alias": "srv-db-01",
                        "matched_via": "alias",
                        "confidence": 0.9,
                    }
                ]
            ),
        ),
        patch("contextedge.services.identity_service.link_node_to_identities", AsyncMock()) as graph_mock,
        patch("contextedge.services.identity_service.append_operational_event", AsyncMock()) as event_mock,
    ):
        refs = await link_evidence_identities(
            db,
            tenant_id=tenant_id,
            evidence=evidence,
            content="Host srv-db-01 restarted successfully",
            source_id=uuid4(),
            source_metadata={"raw_object_id": str(uuid4())},
        )

    assert refs[0]["canonical_id"] == str(identity_id)
    assert evidence.canonical_entity_refs["identities"][0]["canonical_name"] == "srv-db-01"
    assert any(isinstance(obj, EvidenceIdentityLink) for obj in added)
    graph_mock.assert_awaited_once()
    event_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_episodes_from_evidence_aggregates_identity_refs():
    tenant_id = uuid4()
    evidence_id = uuid4()
    identity_id = uuid4()
    added = []

    db = SimpleNamespace(
        execute=AsyncMock(
            return_value=_AllResult(
                [
                    (
                        {
                            "identities": [
                                {
                                    "canonical_id": str(identity_id),
                                    "canonical_name": "sql-prod",
                                    "entity_type": "service",
                                }
                            ]
                        },
                    )
                ]
            )
        ),
        add=lambda obj: added.append(obj),
        flush=AsyncMock(),
        refresh=AsyncMock(),
    )

    with patch(
        "contextedge.services.episode_service.reconstruct_episode",
        AsyncMock(return_value=[{"title": "Database outage", "steps": []}]),
    ):
        episodes = await create_episodes_from_evidence(
            db,
            tenant_id=tenant_id,
            domain_id=None,
            evidence_items=[{"title": "DB outage", "body": "sql-prod down"}],
            evidence_ids=[evidence_id],
        )

    assert len(episodes) == 1
    assert episodes[0].entity_refs["identities"][0]["canonical_id"] == str(identity_id)
    assert any(isinstance(obj, Episode) for obj in added)


def _correlation_db(evidence, source, raw, execute_results):
    added = []
    results = list(execute_results)

    async def _execute(stmt):
        if results:
            return results.pop(0)
        return _ScalarOneOrNoneResult(None)

    db = SimpleNamespace(
        get=AsyncMock(side_effect=[evidence, source, raw]),
        execute=AsyncMock(side_effect=_execute),
        add=lambda obj: added.append(obj),
        flush=AsyncMock(),
    )
    return db, added


def _correlation_fixtures(tenant_id):
    source_id = uuid4()
    evidence = SimpleNamespace(
        id=uuid4(),
        tenant_id=tenant_id,
        source_id=source_id,
        raw_object_ref=uuid4(),
        thread_id=None,
        ingested_at=datetime.now(timezone.utc),
    )
    source = SimpleNamespace(id=source_id, tenant_id=tenant_id, source_type="servicenow")
    raw = SimpleNamespace(external_id=None, raw_payload={})
    return evidence, source, raw


@pytest.mark.asyncio
async def test_correlation_links_shared_device_identity_within_window():
    tenant_id = uuid4()
    identity_id = uuid4()
    other_evidence_id = uuid4()
    evidence, source, raw = _correlation_fixtures(tenant_id)

    db, added = _correlation_db(
        evidence, source, raw,
        [
            _AllResult([(identity_id, "device")]),  # trusted identity types
            _AllResult([(other_evidence_id, identity_id)]),  # shared links
            _AllResult([(other_evidence_id, evidence.ingested_at)]),  # times
            _ScalarOneOrNoneResult(None),  # correlation-edge dedupe
        ],
    )

    with patch(
        "contextedge.services.correlation_service.get_identity_ids_for_evidence",
        AsyncMock(return_value={identity_id}),
    ):
        result = await correlate_evidence_item(db, tenant_id, evidence.id)

    assert result["status"] == "ok"
    assert result["correlations_created"] == 1
    edges = [obj for obj in added if isinstance(obj, CorrelationEdge)]
    assert edges[0].correlation_type == "identity_match"
    assert edges[0].confidence == 0.65


@pytest.mark.asyncio
async def test_correlation_ignores_person_only_single_identity():
    """A lone shared person must not correlate incidents (mega-cluster guard)."""
    tenant_id = uuid4()
    identity_id = uuid4()
    other_evidence_id = uuid4()
    evidence, source, raw = _correlation_fixtures(tenant_id)

    db, added = _correlation_db(
        evidence, source, raw,
        [
            _AllResult([(identity_id, "person")]),
            _AllResult([(other_evidence_id, identity_id)]),
            _AllResult([(other_evidence_id, evidence.ingested_at)]),
        ],
    )

    with patch(
        "contextedge.services.correlation_service.get_identity_ids_for_evidence",
        AsyncMock(return_value={identity_id}),
    ):
        result = await correlate_evidence_item(db, tenant_id, evidence.id)

    assert result["status"] == "skipped"
    assert not any(isinstance(obj, CorrelationEdge) for obj in added)


@pytest.mark.asyncio
async def test_correlation_ignores_identity_outside_time_window():
    tenant_id = uuid4()
    identity_id = uuid4()
    other_evidence_id = uuid4()
    evidence, source, raw = _correlation_fixtures(tenant_id)
    six_months_ago = evidence.ingested_at - timedelta(days=180)

    db, added = _correlation_db(
        evidence, source, raw,
        [
            _AllResult([(identity_id, "device")]),
            _AllResult([(other_evidence_id, identity_id)]),
            _AllResult([(other_evidence_id, six_months_ago)]),
        ],
    )

    with patch(
        "contextedge.services.correlation_service.get_identity_ids_for_evidence",
        AsyncMock(return_value={identity_id}),
    ):
        result = await correlate_evidence_item(db, tenant_id, evidence.id)

    assert result["status"] == "skipped"
    assert not any(isinstance(obj, CorrelationEdge) for obj in added)


@pytest.mark.asyncio
async def test_rank_playbooks_identity_signal_boosts_score():
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
    version = SimpleNamespace(playbook_confidence=0.7)
    db = SimpleNamespace(
        execute=AsyncMock(return_value=_ScalarsResult([playbook])),
        get=AsyncMock(return_value=version),
    )

    with (
        patch("contextedge.search.hybrid_ranker._latest_published_version_id", AsyncMock(return_value=uuid4())),
        patch("contextedge.search.hybrid_ranker._graph_score_for_playbook", AsyncMock(return_value=0.0)),
        patch("contextedge.search.hybrid_ranker._identity_score_for_playbook", AsyncMock(return_value=1.0)),
        patch("contextedge.search.hybrid_ranker._negative_penalty_for_playbook", AsyncMock(return_value=0.0)),
        patch("contextedge.search.hybrid_ranker.resolve_identity_ids_for_terms", AsyncMock(return_value={uuid4()})),
    ):
        ranked = await rank_playbooks(
            db,
            tenant_id=tenant_id,
            query_text="",
            entities=["sql-prod"],
        )

    assert len(ranked) == 1
    assert ranked[0].breakdown["identity"] == 1.0
    assert ranked[0].score > 0.0
