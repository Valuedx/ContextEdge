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

    assert totals == {"tenants": 1, "embedded": 1, "failed": 1}
    db.commit.assert_awaited_once()  # per-tenant commit even with failures
    sql = captured_sql[0]
    assert "embedding IS NULL" in sql
    assert "lifecycle_state" in sql
    assert "NOT IN" in sql
