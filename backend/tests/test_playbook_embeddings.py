"""Playbook semantic fingerprints (migration 0035)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from contextedge.services.playbook_embedding import (
    build_playbook_embedding_text,
    embed_playbook,
)


def _playbook(**kw):
    return SimpleNamespace(
        id=uuid4(),
        tenant_id=uuid4(),
        title=kw.get("title", "Renew VPN certificate and restart RADIUS"),
        description=kw.get("description", "Recovery for expired VPN auth certificates."),
        current_version_id=kw.get("current_version_id"),
        embedding=None,
    )


def _version():
    return SimpleNamespace(
        trigger_conditions={
            "symptoms": [
                "users cannot log in to the VPN",
                "authentication failures across all clients",
            ],
            "error_code": "AUTH_CERT_EXPIRED",
        },
        steps=[
            {"title": "Check certificate expiry on vpn-gw-east-01"},
            {"text": "Renew the certificate"},
            {"action": "Restart the authentication service"},
        ],
    )


def test_embedding_text_includes_symptom_vocabulary():
    """The whole point: symptom-level words the title never contains."""
    text = build_playbook_embedding_text(_playbook(), _version())
    assert "users cannot log in" in text
    assert "AUTH_CERT_EXPIRED" in text
    assert "Check certificate expiry" in text
    assert "Renew VPN certificate" in text  # title still present


def test_embedding_text_without_version_uses_title_and_description():
    text = build_playbook_embedding_text(_playbook(), None)
    assert "Renew VPN certificate" in text
    assert "expired VPN auth certificates" in text


def test_embedding_text_is_bounded():
    version = SimpleNamespace(
        trigger_conditions={"blob": "x" * 50_000},
        steps=[{"title": "y" * 5_000}] * 30,
    )
    text = build_playbook_embedding_text(_playbook(), version)
    assert len(text) <= 4_000


@pytest.mark.asyncio
async def test_embed_playbook_failure_is_soft():
    playbook = _playbook()
    db = SimpleNamespace(get=AsyncMock(return_value=None))

    with patch(
        "contextedge.ai.provider.generate_embedding",
        AsyncMock(side_effect=RuntimeError("provider down")),
    ):
        ok = await embed_playbook(db, playbook, _version())

    assert ok is False
    assert playbook.embedding is None
    assert playbook.lexical_search_text


@pytest.mark.asyncio
async def test_embed_playbook_sets_embedding():
    playbook = _playbook()
    db = SimpleNamespace(get=AsyncMock(return_value=None))

    with patch(
        "contextedge.ai.provider.generate_embedding",
        AsyncMock(return_value=[0.1] * 3072),
    ):
        ok = await embed_playbook(db, playbook, _version())

    assert ok is True
    assert playbook.embedding is not None
    assert playbook.lexical_search_text


def test_migration_0035_declares_index_and_chain():
    import importlib.util
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[1]
        / "alembic" / "versions" / "0035_playbook_embeddings.py"
    )
    spec = importlib.util.spec_from_file_location("mig_0035", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.INDEX_NAME == "ix_playbooks_embedding_halfvec_hnsw"
    assert module.down_revision == "0034_execution_run_updated_at"


def test_migration_0086_to_0090_chain():
    import importlib.util
    from pathlib import Path

    versions = Path(__file__).resolve().parents[1] / "alembic" / "versions"
    expected = [
        ("0086_playbook_lexical_search.py", "0086_playbook_lexical_search", "0085_playbook_risk_tier_check"),
        ("0087_playbook_negative_knowledge.py", "0087_playbook_negative_knowledge", "0086_playbook_lexical_search"),
        ("0088_runtime_match_records.py", "0088_runtime_match_records", "0087_playbook_negative_knowledge"),
        ("0089_retrieval_feedback_version.py", "0089_retrieval_feedback_version", "0088_runtime_match_records"),
        ("0090_ranking_calibration_configs.py", "0090_ranking_calibration_configs", "0089_retrieval_feedback_version"),
    ]
    for filename, revision, down in expected:
        spec = importlib.util.spec_from_file_location(revision, versions / filename)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        assert module.revision == revision
        assert module.down_revision == down


def test_migration_0085_declares_risk_tier_check_and_chain():
    import importlib.util
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[1]
        / "alembic" / "versions" / "0085_playbook_risk_tier_check.py"
    )
    spec = importlib.util.spec_from_file_location("mig_0085", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.revision == "0085_playbook_risk_tier_check"
    assert module.down_revision == "0084_fill_null_tenant_ids"
    assert module._CONSTRAINT == "ck_playbooks_risk_tier"


@pytest.mark.asyncio
async def test_backfill_targets_only_null_nonterminal_rows():
    """The backfill must skip already-embedded rows and terminal states
    (retired / deprecated can never reach "approved" again)."""
    from unittest.mock import Mock

    from contextedge.workers.playbook_tasks import _backfill

    playbook_ok = _playbook()
    playbook_fail = _playbook()
    captured_sql = []

    async def execute(stmt):
        captured_sql.append(str(stmt))
        result = Mock()
        result.scalars.return_value.all.return_value = [playbook_ok, playbook_fail]
        return result

    db = SimpleNamespace(execute=execute, commit=AsyncMock())

    with patch(
        "contextedge.workers.playbook_tasks.embed_playbook",
        AsyncMock(side_effect=[True, False]),
    ):
        totals = await _backfill(db, str(uuid4()), limit=200)

    assert totals["tenants"] == 1
    assert totals["embedded"] == 1
    assert totals["failed"] == 1
    assert totals["refresh_stale"] is False
    db.commit.assert_awaited_once()  # per-tenant commit even with failures
    sql = captured_sql[0]
    assert "embedding IS NULL" in sql
    assert "lifecycle_state" in sql
    assert "NOT IN" in sql


@pytest.mark.asyncio
async def test_stale_backfill_includes_approved_with_published_version():
    from unittest.mock import Mock

    from contextedge.workers.playbook_tasks import _backfill

    playbook_ok = _playbook()
    captured_sql = []

    async def execute(stmt):
        captured_sql.append(str(stmt))
        result = Mock()
        result.scalars.return_value.all.return_value = [playbook_ok]
        return result

    db = SimpleNamespace(execute=execute, commit=AsyncMock())

    with patch(
        "contextedge.workers.playbook_tasks.embed_playbook",
        AsyncMock(return_value=True),
    ):
        totals = await _backfill(
            db, str(uuid4()), limit=200, refresh_stale=True
        )

    assert totals["embedded"] == 1
    assert totals["refresh_stale"] is True
    sql = " ".join(captured_sql)
    assert "published_at" in sql or "playbook_versions" in sql.lower()


def test_beat_schedule_includes_playbook_embedding_backfill():
    from contextedge.workers.celery_app import celery_app

    entry = celery_app.conf.beat_schedule["backfill-playbook-embeddings-nightly"]
    assert entry["task"] == "evaluation.backfill_playbook_embeddings"
    assert entry["args"] == ("all", 200, True)
