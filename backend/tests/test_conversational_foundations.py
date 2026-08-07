"""Conversational foundations: reconstruction debounce + Teams metadata."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import pytest

from contextedge.connectors.teams.connector import _message_content

NOW = datetime(2026, 8, 1, 12, 0, 0, tzinfo=UTC)


# --- debounce ---------------------------------------------------------------


def _scalar_one(value):
    result = Mock()
    result.scalar_one_or_none.return_value = value
    return result


@pytest.mark.asyncio
async def test_reconstruct_defers_while_cluster_is_unsettled():
    """A cluster still receiving evidence must not spend an LLM call â€”
    the later-scheduled task from the newer evidence will handle it."""
    from contextedge.services.episode_cluster_service import EpisodeCluster
    from contextedge.workers.extraction_tasks import _reconstruct

    tenant_id = uuid4()
    seed = uuid4()
    cluster = EpisodeCluster(
        fingerprint="fp-busy", evidence_ids=[seed, uuid4()], reasons={str(seed): ["seed"]}
    )
    # Newest member ingested 30 seconds ago (inside the window); oldest
    # 5 minutes ago (not yet overdue for the starvation guard).
    bounds_result = Mock()
    bounds_result.first.return_value = (
        datetime.now(UTC) - timedelta(minutes=5),
        datetime.now(UTC) - timedelta(seconds=30),
    )
    db = SimpleNamespace(execute=AsyncMock(return_value=bounds_result))

    with patch(
        "contextedge.services.episode_cluster_service.resolve_episode_cluster",
        AsyncMock(return_value=cluster),
    ):
        result = await _reconstruct(db, str(seed), tenant_id)

    assert result["status"] == "deferred_unsettled"
    assert db.execute.await_count == 2  # advisory lock + settlement query


@pytest.mark.asyncio
async def test_reconstruct_settle_bypass_for_manual_triggers():
    """settle=False (explicit reviewer request) skips the settlement
    query entirely and proceeds to the idempotency check."""
    from contextedge.services.episode_cluster_service import EpisodeCluster
    from contextedge.workers.extraction_tasks import _reconstruct

    tenant_id = uuid4()
    seed = uuid4()
    existing_draft = uuid4()
    cluster = EpisodeCluster(
        fingerprint="fp-manual", evidence_ids=[seed], reasons={str(seed): ["seed"]}
    )
    db = SimpleNamespace(execute=AsyncMock(return_value=_scalar_one(existing_draft)))

    with patch(
        "contextedge.services.episode_cluster_service.resolve_episode_cluster",
        AsyncMock(return_value=cluster),
    ):
        result = await _reconstruct(db, str(seed), tenant_id, settle=False)

    # Executes: advisory lock, then the fingerprint idempotency check.
    assert result["status"] == "duplicate_cluster"
    assert db.execute.await_count == 2


def test_correlation_dispatch_is_debounced():
    import pathlib

    source = (
        pathlib.Path(__file__).resolve().parents[1]
        / "src" / "contextedge" / "workers" / "correlation_tasks.py"
    ).read_text(encoding="utf-8")
    assert "apply_async" in source
    assert "countdown=RECONSTRUCT_DEBOUNCE_SECONDS" in source
    # The immediate-fire form must be gone from the correlate path.
    assert "reconstruct_episode_task.delay" not in source


def test_manual_reconstruction_bypasses_settle():
    import pathlib

    source = (
        pathlib.Path(__file__).resolve().parents[1]
        / "src" / "contextedge" / "api" / "v1" / "episodes.py"
    ).read_text(encoding="utf-8")
    assert "settle=False" in source


# --- Teams metadata capture -------------------------------------------------


def _graph_message(**kw):
    msg = {
        "id": kw.get("id", "msg-1"),
        "replyToId": kw.get("reply_to"),
        "messageType": kw.get("message_type", "message"),
        "createdDateTime": "2026-08-01T10:00:00Z",
        "body": {"content": kw.get("body", "Ordering DB down"), "contentType": "text"},
        "subject": kw.get("subject"),
        "importance": "normal",
        "from": kw.get(
            "from_block",
            {"user": {"displayName": "Ravi Kumar", "email": "ravi@acme.example"}},
        ),
    }
    msg.update(kw.get("extra", {}))
    return msg


def test_message_content_captures_reply_and_human_identity():
    content = _message_content(_graph_message(reply_to="root-7"))
    assert content["message_id"] == "msg-1"
    assert content["reply_to_id"] == "root-7"
    assert content["is_bot"] is False
    assert content["from"] == "Ravi Kumar"
    assert content["is_deleted"] is False
    assert "from_application" not in content


def test_message_content_detects_bots():
    content = _message_content(
        _graph_message(
            from_block={"application": {"displayName": "ServiceNow Notifications"}}
        )
    )
    assert content["is_bot"] is True
    assert content["from_application"] == "ServiceNow Notifications"
    assert content["from"] is None  # bot cards are not human assertions


def test_message_content_preserves_edits_deletes_attachments_mentions():
    content = _message_content(
        _graph_message(
            extra={
                "lastEditedDateTime": "2026-08-01T10:05:00Z",
                "deletedDateTime": "2026-08-01T11:00:00Z",
                "attachments": [
                    {"name": "error.png", "contentType": "image/png"},
                    "junk",
                ],
                "mentions": [
                    {"mentioned": {"user": {"displayName": "John Mathew"}}},
                    {"mentioned": {}},
                ],
            }
        )
    )
    assert content["last_edited_at"] == "2026-08-01T10:05:00Z"
    assert content["is_deleted"] is True
    assert content["deleted_at"] == "2026-08-01T11:00:00Z"
    assert content["attachments"] == [{"name": "error.png", "content_type": "image/png"}]
    assert content["mentions"] == ["John Mathew"]


def test_message_content_tolerates_minimal_payloads():
    content = _message_content({"id": "m", "body": {}})
    assert content["message_id"] == "m"
    assert content["reply_to_id"] is None
    assert content["is_bot"] is False
    assert content["body"] == ""


@pytest.mark.asyncio
async def test_starvation_guard_forces_synthesis_on_never_quiet_clusters():
    """A channel that never goes quiet must still get its first
    synthesis within the max delay â€” a long live incident is exactly
    when episodes matter."""
    from contextedge.services.episode_cluster_service import EpisodeCluster
    from contextedge.workers.extraction_tasks import _reconstruct

    tenant_id = uuid4()
    seed = uuid4()
    cluster = EpisodeCluster(
        fingerprint="fp-storm", evidence_ids=[seed, uuid4()], reasons={str(seed): ["seed"]}
    )
    bounds_result = Mock()
    # Oldest member 40 minutes old (overdue), newest 10 seconds (unsettled).
    bounds_result.first.return_value = (
        datetime.now(UTC) - timedelta(minutes=40),
        datetime.now(UTC) - timedelta(seconds=10),
    )
    dup_result = Mock()
    dup_result.scalar_one_or_none.return_value = uuid4()  # stop at idempotency

    lock_result = Mock(); lock_result.scalar.return_value = True
    db = SimpleNamespace(execute=AsyncMock(side_effect=[lock_result, bounds_result, dup_result]))

    with patch(
        "contextedge.services.episode_cluster_service.resolve_episode_cluster",
        AsyncMock(return_value=cluster),
    ):
        result = await _reconstruct(db, str(seed), tenant_id)

    # Not deferred: the guard pushed past settlement into the normal flow.
    assert result["status"] == "duplicate_cluster"
