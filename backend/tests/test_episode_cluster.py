"""P0: cluster materialization, source provenance, per-episode membership."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import UUID, uuid4

import pytest

from contextedge.services.episode_cluster_service import (
    CLUSTER_TIME_WINDOW,
    MAX_CLUSTER_SIZE,
    _within_window,
    cluster_fingerprint,
    resolve_episode_cluster,
)

NOW = datetime(2026, 8, 1, 12, 0, 0, tzinfo=UTC)


def _rows_result(rows):
    result = Mock()
    result.all.return_value = rows
    return result


def _scalars_result(values):
    result = Mock()
    result.scalars.return_value.all.return_value = values
    return result


def _scalar_one(value):
    result = Mock()
    result.scalar_one_or_none.return_value = value
    return result


# --- cluster resolution -----------------------------------------------------


@pytest.mark.asyncio
async def test_cluster_expands_through_case_links_and_correlations():
    """Seed = ServiceNow incident; the Teams thread joins via a
    correlation edge and the email via the shared canonical case — one
    multi-source cluster instead of a cluster of one."""
    tenant_id = uuid4()
    incident, teams, email = uuid4(), uuid4(), uuid4()
    case_id = uuid4()

    call = {"n": 0}

    async def execute(stmt):
        call["n"] += 1
        text = str(stmt)
        if "evidence_items" in text:
            # visibility+times: first for seed, then for discovered
            if call["n"] == 1:
                return _rows_result([(incident, NOW)])
            return _rows_result([(teams, NOW + timedelta(minutes=10)),
                                 (email, NOW + timedelta(minutes=20))])
        if text.startswith("SELECT case_links.evidence_id, case_links.canonical_case_id"):
            return _rows_result([(email, case_id)])  # cases → members
        if text.startswith("SELECT case_links.canonical_case_id"):
            return _scalars_result([case_id])  # frontier → cases
        if "correlation_edges" in text:
            return _rows_result([(incident, teams, "case_link_match")])
        return _rows_result([])

    cluster = await resolve_episode_cluster(
        SimpleNamespace(execute=execute), tenant_id, [incident]
    )

    assert set(cluster.evidence_ids) == {incident, teams, email}
    assert cluster.reasons[str(incident)] == ["seed"]
    assert any(r.startswith("correlation:") for r in cluster.reasons[str(teams)])
    assert any(r.startswith("case:") for r in cluster.reasons[str(email)])
    assert cluster.fingerprint == cluster_fingerprint(cluster.evidence_ids)


@pytest.mark.asyncio
async def test_cluster_enforces_temporal_fence():
    """A correlation chain must not drag in last quarter's ticket: the
    candidate is connected but 90 days from every seed."""
    tenant_id = uuid4()
    seed, old = uuid4(), uuid4()

    call = {"n": 0}

    async def execute(stmt):
        call["n"] += 1
        text = str(stmt)
        if "evidence_items" in text:
            if call["n"] == 1:
                return _rows_result([(seed, NOW)])
            return _rows_result([(old, NOW - timedelta(days=90))])
        if "correlation_edges" in text:
            return _rows_result([(seed, old, "identity_match")])
        if "canonical_case_id" in text:
            return _scalars_result([])
        return _rows_result([])

    cluster = await resolve_episode_cluster(
        SimpleNamespace(execute=execute), tenant_id, [seed]
    )
    assert cluster.evidence_ids == [seed]  # old ticket fenced out


def test_temporal_window_rule_fails_open_for_undated():
    assert _within_window(None, [NOW])
    assert _within_window(NOW, [])
    assert _within_window(NOW + CLUSTER_TIME_WINDOW, [NOW])
    assert not _within_window(NOW + CLUSTER_TIME_WINDOW + timedelta(days=1), [NOW])


@pytest.mark.asyncio
async def test_cluster_size_cap_marks_truncation():
    tenant_id = uuid4()
    seed = uuid4()
    flood = [uuid4() for _ in range(MAX_CLUSTER_SIZE + 20)]

    call = {"n": 0}

    async def execute(stmt):
        call["n"] += 1
        text = str(stmt)
        if "evidence_items" in text:
            if call["n"] == 1:
                return _rows_result([(seed, NOW)])
            return _rows_result([(eid, NOW) for eid in flood])
        if "correlation_edges" in text:
            return _rows_result([(seed, eid, "case_link_match") for eid in flood])
        if "canonical_case_id" in text:
            return _scalars_result([])
        return _rows_result([])

    cluster = await resolve_episode_cluster(
        SimpleNamespace(execute=execute), tenant_id, [seed]
    )
    assert len(cluster.evidence_ids) == MAX_CLUSTER_SIZE
    assert cluster.truncated is True


def test_fingerprint_is_order_independent():
    a, b = uuid4(), uuid4()
    assert cluster_fingerprint([a, b]) == cluster_fingerprint([b, a])
    assert cluster_fingerprint([a]) != cluster_fingerprint([a, b])


# --- extractor ref translation ----------------------------------------------


def test_ref_translation_drops_minted_evidence():
    from contextedge.ai.extractors.episode_extractor import _translate_refs

    real = str(uuid4())
    ref_map = {"ev-1": real}
    assert _translate_refs(["ev-1", "[ev-1]", "ev-9", "junk"], ref_map) == [real, real]
    assert _translate_refs(["ev-9"], ref_map) is None
    assert _translate_refs("not-a-list", ref_map) is None


def test_evidence_block_labels_refs_and_roles():
    from contextedge.ai.extractors.episode_extractor import _format_evidence_block

    block = _format_evidence_block(
        [
            {
                "source_type": "teams",
                "source_role": "working_discussion",
                "title": "VPN bridge",
                "body": "cert expired",
                "timestamp": "2026-08-01",
                "evidence_id": str(uuid4()),
            }
        ]
    )
    assert "[ev-1]" in block
    assert "Source: teams (working_discussion)" in block


def test_episode_prompt_v2_is_default_and_requests_refs():
    from contextedge.ai.prompts import get_prompt

    prompt = get_prompt("episode", None)
    assert prompt.version == "v2"
    assert "evidence_refs" in prompt.system
    # v1's doubled-brace bug must not carry into v2 (system is never
    # .format()ed, so {{ would reach the model literally).
    assert "{{" not in prompt.system


# --- per-episode membership persistence --------------------------------------


@pytest.mark.asyncio
async def test_split_episodes_get_only_their_cited_evidence():
    from contextedge.services.episode_service import create_episodes_from_evidence

    tenant_id = uuid4()
    vpn_a, vpn_b, payroll = uuid4(), uuid4(), uuid4()
    evidence_ids = [vpn_a, vpn_b, payroll]

    extracted = [
        {
            "title": "VPN certificate failure",
            "overall_confidence": 0.9,
            "evidence_refs": [str(vpn_a), str(vpn_b)],
            "steps": [],
        },
        {
            "title": "Payroll email issue",
            "overall_confidence": 0.7,
            "evidence_refs": [str(payroll)],
            "steps": [],
        },
    ]

    added = []
    entity_rows = _rows_result([(eid, None) for eid in evidence_ids])
    db = SimpleNamespace(
        execute=AsyncMock(return_value=entity_rows),
        add=added.append,
        flush=AsyncMock(),
        refresh=AsyncMock(),
    )

    with (
        patch(
            "contextedge.services.episode_service.reconstruct_episode",
            AsyncMock(return_value=extracted),
        ),
        patch(
            "contextedge.services.episode_service.generate_embedding",
            AsyncMock(return_value=[0.1] * 3072),
        ),
    ):
        episodes = await create_episodes_from_evidence(
            db,
            tenant_id=tenant_id,
            domain_id=None,
            evidence_items=[{"evidence_id": str(e)} for e in evidence_ids],
            evidence_ids=evidence_ids,
            cluster_fingerprint="fp-123",
            cluster_reasons={str(vpn_a): ["seed"]},
        )

    vpn_episode, payroll_episode = episodes
    assert set(vpn_episode.evidence_ids) == {str(vpn_a), str(vpn_b)}
    assert payroll_episode.evidence_ids == [str(payroll)]  # NOT all three
    assert vpn_episode.cluster_fingerprint == "fp-123"

    from contextedge.models.episode import EpisodeEvidenceLink

    links = [obj for obj in added if isinstance(obj, EpisodeEvidenceLink)]
    assert len(links) == 3  # 2 for VPN + 1 for payroll
    assert {link.evidence_id for link in links} == {vpn_a, vpn_b, payroll}
    seed_link = next(link for link in links if link.evidence_id == vpn_a)
    assert seed_link.link_reason == "seed"


@pytest.mark.asyncio
async def test_missing_citations_fall_back_to_full_cluster():
    from contextedge.services.episode_service import create_episodes_from_evidence

    tenant_id = uuid4()
    evidence_ids = [uuid4(), uuid4()]
    extracted = [{"title": "No refs", "overall_confidence": 0.5, "steps": []}]

    added = []
    db = SimpleNamespace(
        execute=AsyncMock(return_value=_rows_result([(eid, None) for eid in evidence_ids])),
        add=added.append,
        flush=AsyncMock(),
        refresh=AsyncMock(),
    )
    with (
        patch(
            "contextedge.services.episode_service.reconstruct_episode",
            AsyncMock(return_value=extracted),
        ),
        patch(
            "contextedge.services.episode_service.generate_embedding",
            AsyncMock(return_value=[0.1] * 3072),
        ),
    ):
        (episode,) = await create_episodes_from_evidence(
            db,
            tenant_id=tenant_id,
            domain_id=None,
            evidence_items=[{"evidence_id": str(e)} for e in evidence_ids],
            evidence_ids=evidence_ids,
        )
    assert set(episode.evidence_ids) == {str(e) for e in evidence_ids}


# --- reconstruction flow ------------------------------------------------------


@pytest.mark.asyncio
async def test_reconstruct_is_idempotent_per_cluster():
    from contextedge.workers.extraction_tasks import _reconstruct

    tenant_id = uuid4()
    seed = uuid4()
    existing_draft_id = uuid4()

    from contextedge.services.episode_cluster_service import EpisodeCluster

    cluster = EpisodeCluster(
        fingerprint="fp-same", evidence_ids=[seed], reasons={str(seed): ["seed"]}
    )
    db = SimpleNamespace(execute=AsyncMock(return_value=_scalar_one(existing_draft_id)))

    with patch(
        "contextedge.services.episode_cluster_service.resolve_episode_cluster",
        AsyncMock(return_value=cluster),
    ):
        result = await _reconstruct(db, str(seed), tenant_id)

    assert result["status"] == "duplicate_cluster"
    assert result["episode_ids"] == [str(existing_draft_id)]


@pytest.mark.asyncio
async def test_reconstruct_passes_real_source_types_and_supersedes_subset_drafts():
    from contextedge.workers.extraction_tasks import _reconstruct

    tenant_id = uuid4()
    ticket_ev = SimpleNamespace(
        id=uuid4(), tenant_id=tenant_id, title="INC0010427", body_text="VPN down",
        created_at_source=NOW, ingested_at=NOW, source_id=uuid4(),
        sensitivity_label=None,
    )
    teams_ev = SimpleNamespace(
        id=uuid4(), tenant_id=tenant_id, title="bridge", body_text="cert expired",
        created_at_source=NOW + timedelta(minutes=5), ingested_at=NOW, source_id=uuid4(),
        sensitivity_label=None,
    )
    old_draft = SimpleNamespace(
        evidence_ids=[str(ticket_ev.id)], reviewer_state="pending_review",
        cluster_fingerprint="fp-old",
    )

    from contextedge.services.episode_cluster_service import EpisodeCluster

    cluster = EpisodeCluster(
        fingerprint="fp-new",
        evidence_ids=[ticket_ev.id, teams_ev.id],
        reasons={str(ticket_ev.id): ["seed"], str(teams_ev.id): ["correlation:case_link_match"]},
    )

    async def execute(stmt):
        text = str(stmt)
        if "cluster_fingerprint = " in text or "cluster_fingerprint =" in text:
            return _scalar_one(None)  # no duplicate draft
        if "domains" in text:
            return _scalar_one(None)
        if "sources" in text:
            return _rows_result([(ticket_ev.id, "servicenow"), (teams_ev.id, "teams")])
        if "reviewer_state" in text:
            return _scalars_result([old_draft])
        return _rows_result([])

    async def get(model, pk):
        return {ticket_ev.id: ticket_ev, teams_ev.id: teams_ev}.get(pk)

    db = SimpleNamespace(execute=execute, get=AsyncMock(side_effect=get), flush=AsyncMock())

    captured_items = {}

    async def fake_create(db_, **kwargs):
        captured_items.update(kwargs)
        return []

    with (
        patch(
            "contextedge.services.episode_cluster_service.resolve_episode_cluster",
            AsyncMock(return_value=cluster),
        ),
        patch(
            "contextedge.services.episode_service.create_episodes_from_evidence",
            side_effect=fake_create,
        ),
    ):
        result = await _reconstruct(db, str(ticket_ev.id), tenant_id)

    items = captured_items["evidence_items"]
    assert [i["source_type"] for i in items] == ["servicenow", "teams"]
    assert [i["source_role"] for i in items] == ["ticket", "working_discussion"]
    assert captured_items["cluster_fingerprint"] == "fp-new"
    assert old_draft.reviewer_state == "superseded"  # subset draft retired
    assert result["superseded_drafts"] == 1
