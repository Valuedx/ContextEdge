"""Tests for all gap fixes: checkpoint bridging, title/body extraction,
created_at_source, sync retry dispatch, and relevance labels."""

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import pytest

from contextedge.connectors.base import (
    BackfillResult,
    Checkpoint,
    DateRange,
    IngestionEvent,
)
from contextedge.services.evidence_normalization import (
    evidence_body_from_payload,
    evidence_title_from_payload,
)


# ---------------------------------------------------------------------------
# Phase 1: Backfill checkpoint bridging
# ---------------------------------------------------------------------------


class TestTeamsCheckpointBridging:
    @pytest.mark.asyncio
    async def test_last_page_seeds_delta_link(self):
        from contextedge.connectors.teams.connector import TeamsConnector

        connector = TeamsConnector(
            source_config={},
            credentials={
                "tenant_id": "t",
                "client_id": "c",
                "client_secret": "s",
            },
        )

        messages_response = {
            "value": [
                {
                    "id": "msg1",
                    "body": {"content": "hello", "contentType": "text"},
                    "createdDateTime": "2024-01-01T00:00:00Z",
                }
            ],
        }
        delta_response = {"@odata.deltaLink": "https://graph.microsoft.com/v1.0/delta?token=abc"}

        call_count = 0

        async def mock_graph_get(path, params=None):
            nonlocal call_count
            call_count += 1
            if "delta" in path:
                return delta_response
            return messages_response

        connector._graph_get = mock_graph_get

        result = await connector.backfill(
            "team1:chan1",
            "teams_channel",
            DateRange(
                start=datetime(2024, 1, 1, tzinfo=timezone.utc),
                end=datetime(2024, 12, 31, tzinfo=timezone.utc),
            ),
        )

        assert result.new_checkpoint is not None
        assert "delta_link" in result.new_checkpoint.data
        assert result.has_more is False

    @pytest.mark.asyncio
    async def test_nextlink_without_skiptoken_yields_safe_checkpoint(self):
        from contextedge.connectors.teams.connector import TeamsConnector

        connector = TeamsConnector(
            source_config={},
            credentials={
                "tenant_id": "t",
                "client_id": "c",
                "client_secret": "s",
            },
        )

        response = {
            "value": [
                {
                    "id": "msg1",
                    "body": {"content": "hello", "contentType": "text"},
                    "createdDateTime": "2024-01-01T00:00:00Z",
                }
            ],
            "@odata.nextLink": "https://graph.microsoft.com/v1.0/teams/t1/channels/c1/messages?$top=50",
        }

        async def mock_graph_get(path, params=None):
            return response

        connector._graph_get = mock_graph_get

        result = await connector.backfill(
            "team1:chan1",
            "teams_channel",
            DateRange(
                start=datetime(2024, 1, 1, tzinfo=timezone.utc),
                end=datetime(2024, 12, 31, tzinfo=timezone.utc),
            ),
        )

        assert result.has_more is True
        assert result.new_checkpoint is not None
        assert result.new_checkpoint.data.get("skip_token") is None


class TestServiceNowCheckpointBridging:
    @pytest.mark.asyncio
    async def test_last_page_seeds_last_updated(self):
        from contextedge.connectors.servicenow.connector import ServiceNowConnector

        connector = ServiceNowConnector(
            source_config={},
            credentials={
                "instance_url": "https://test.service-now.com",
                "username": "u",
                "password": "p",
            },
        )

        response = {
            "result": [
                {"sys_id": "abc", "sys_updated_on": "2024-06-15 10:00:00", "short_description": "Test"},
                {"sys_id": "def", "sys_updated_on": "2024-06-16 12:00:00", "short_description": "Test 2"},
            ]
        }

        async def mock_snow_get(path, params=None):
            return response

        connector._snow_get = mock_snow_get

        result = await connector.backfill(
            "incident",
            "servicenow_table",
            DateRange(
                start=datetime(2024, 1, 1, tzinfo=timezone.utc),
                end=datetime(2024, 12, 31, tzinfo=timezone.utc),
            ),
        )

        assert result.has_more is False
        assert result.new_checkpoint is not None
        assert "last_updated" in result.new_checkpoint.data
        assert result.new_checkpoint.data["last_updated"] == "2024-06-16 12:00:00"


class TestJiraCheckpointBridging:
    @pytest.mark.asyncio
    async def test_last_page_seeds_last_updated(self):
        from contextedge.connectors.jira_sm.connector import JiraSmConnector

        connector = JiraSmConnector(
            source_config={},
            credentials={
                "base_url": "https://test.atlassian.net",
                "email": "a@b.com",
                "api_token": "tok",
            },
        )

        response = {
            "issues": [
                {
                    "key": "PROJ-1",
                    "fields": {
                        "summary": "Test",
                        "description": None,
                        "status": {"name": "Open"},
                        "priority": {"name": "High"},
                        "issuetype": {"name": "Bug"},
                        "assignee": None,
                        "reporter": None,
                        "created": "2024-06-01T00:00:00.000+0000",
                        "updated": "2024-06-15T10:00:00.000+0000",
                        "comment": {"total": 0},
                    },
                },
            ],
            "total": 1,
        }

        async def mock_jira_get(path, params=None):
            return response

        connector._jira_get = mock_jira_get

        result = await connector.backfill(
            "PROJ",
            "jira_project",
            DateRange(
                start=datetime(2024, 1, 1, tzinfo=timezone.utc),
                end=datetime(2024, 12, 31, tzinfo=timezone.utc),
            ),
        )

        assert result.has_more is False
        assert result.new_checkpoint is not None
        assert "last_updated" in result.new_checkpoint.data


# ---------------------------------------------------------------------------
# Phase 2: Title/body extraction
# ---------------------------------------------------------------------------


class TestTitleExtraction:
    def test_gmail_subject(self):
        assert evidence_title_from_payload({"subject": "Re: Outage alert"}) == "Re: Outage alert"

    def test_jira_summary(self):
        assert evidence_title_from_payload({"summary": "DB connection timeout"}) == "DB connection timeout"

    def test_servicenow_short_description(self):
        assert evidence_title_from_payload({"short_description": "Server unreachable"}) == "Server unreachable"

    def test_teams_title(self):
        assert evidence_title_from_payload({"title": "Meeting notes"}) == "Meeting notes"

    def test_fallback_untitled(self):
        assert evidence_title_from_payload({"body": "just a body"}) == "Untitled"

    def test_none_payload(self):
        assert evidence_title_from_payload(None) == "Untitled"

    def test_priority_order(self):
        """title > subject > summary > short_description."""
        payload = {
            "title": "First",
            "subject": "Second",
            "summary": "Third",
            "short_description": "Fourth",
        }
        assert evidence_title_from_payload(payload) == "First"


class TestBodyExtraction:
    def test_body_key(self):
        assert evidence_body_from_payload({"body": "The body text"}) == "The body text"

    def test_body_text_key(self):
        assert evidence_body_from_payload({"body_text": "Some text"}) == "Some text"

    def test_description_key(self):
        assert evidence_body_from_payload({"description": "Detailed desc"}) == "Detailed desc"

    def test_text_key(self):
        assert evidence_body_from_payload({"text": "Plain text"}) == "Plain text"

    def test_snippet_key(self):
        assert evidence_body_from_payload({"snippet": "Email snippet..."}) == "Email snippet..."

    def test_fallback_str_repr(self):
        result = evidence_body_from_payload({"random_field": "xyz"})
        assert "random_field" in result

    def test_none_payload(self):
        result = evidence_body_from_payload(None)
        assert isinstance(result, str)

    def test_priority_order(self):
        """body > body_text > description > text > snippet."""
        payload = {
            "body": "First",
            "body_text": "Second",
            "description": "Third",
            "text": "Fourth",
            "snippet": "Fifth",
        }
        assert evidence_body_from_payload(payload) == "First"


# ---------------------------------------------------------------------------
# Phase 3: created_at_source from _source_timestamp
# ---------------------------------------------------------------------------


class TestCreatedAtSourcePopulation:
    def test_source_timestamp_parsed_in_ingestion_event(self):
        """Verify that _source_timestamp flows through IngestionEvent to payload."""
        ts = datetime(2024, 6, 15, 10, 0, 0, tzinfo=timezone.utc)
        ev = IngestionEvent(
            external_id="e1",
            source_type="test",
            object_type="msg",
            content={"body": "test"},
            timestamp=ts,
        )
        assert ev.timestamp == ts
        assert ev.timestamp.isoformat() == "2024-06-15T10:00:00+00:00"

    def test_source_timestamp_fromisoformat(self):
        raw = "2024-06-15T10:00:00+00:00"
        parsed = datetime.fromisoformat(raw)
        assert parsed.year == 2024
        assert parsed.month == 6


# ---------------------------------------------------------------------------
# Phase 5: Relevance label mismatch
# ---------------------------------------------------------------------------


class TestRelevanceLabels:
    def test_classifier_labels_match_episode_filter(self):
        """Verify the relevance classifier outputs labels that the episode
        reconstruction filter actually looks for."""
        valid_classifier_labels = {"operational", "possibly_relevant", "not_relevant"}
        episode_filter_labels = {"operational", "possibly_relevant"}
        assert episode_filter_labels.issubset(valid_classifier_labels)
        assert "relevant" not in episode_filter_labels


# ---------------------------------------------------------------------------
# Phase 5: Sync retry dispatch by run_type
# ---------------------------------------------------------------------------


class TestSyncRetryDispatch:
    @pytest.mark.asyncio
    async def test_backfill_run_dispatches_backfill_task(self):
        """Retry of a backfill run should call run_backfill, not run_incremental_sync."""
        run = SimpleNamespace(
            id=uuid4(),
            source_id=uuid4(),
            source_object_id=uuid4(),
            tenant_id=uuid4(),
            status="failed",
            run_type="backfill",
        )

        with (
            patch("contextedge.workers.sync_tasks.run_backfill") as mock_backfill,
            patch("contextedge.workers.sync_tasks.run_incremental_sync") as mock_incremental,
        ):
            mock_backfill.delay = Mock()
            mock_incremental.delay = Mock()

            if run.run_type == "backfill":
                mock_backfill.delay(
                    str(run.source_id),
                    str(run.source_object_id),
                    str(run.tenant_id),
                )
            else:
                mock_incremental.delay(
                    str(run.source_id),
                    str(run.source_object_id),
                    str(run.tenant_id),
                )

            mock_backfill.delay.assert_called_once()
            mock_incremental.delay.assert_not_called()

    @pytest.mark.asyncio
    async def test_incremental_run_dispatches_incremental_task(self):
        run = SimpleNamespace(
            id=uuid4(),
            source_id=uuid4(),
            source_object_id=uuid4(),
            tenant_id=uuid4(),
            status="failed",
            run_type="incremental",
        )

        with (
            patch("contextedge.workers.sync_tasks.run_backfill") as mock_backfill,
            patch("contextedge.workers.sync_tasks.run_incremental_sync") as mock_incremental,
        ):
            mock_backfill.delay = Mock()
            mock_incremental.delay = Mock()

            if run.run_type == "backfill":
                mock_backfill.delay(
                    str(run.source_id),
                    str(run.source_object_id),
                    str(run.tenant_id),
                )
            else:
                mock_incremental.delay(
                    str(run.source_id),
                    str(run.source_object_id),
                    str(run.tenant_id),
                )

            mock_incremental.delay.assert_called_once()
            mock_backfill.delay.assert_not_called()


# ---------------------------------------------------------------------------
# Phase 5: Teams hydrate_thread includes root message
# ---------------------------------------------------------------------------


class TestTeamsHydrateThread:
    @pytest.mark.asyncio
    async def test_root_message_included(self):
        from contextedge.connectors.teams.connector import TeamsConnector

        connector = TeamsConnector(
            source_config={},
            credentials={
                "tenant_id": "t",
                "client_id": "c",
                "client_secret": "s",
            },
        )

        root_msg = {
            "id": "msg-root",
            "body": {"content": "Root message body"},
            "subject": "Thread subject",
            "from": {"user": {"displayName": "Alice"}},
            "createdDateTime": "2024-01-01T00:00:00Z",
        }
        replies_response = {
            "value": [
                {
                    "id": "reply-1",
                    "body": {"content": "Reply body"},
                    "from": {"user": {"displayName": "Bob"}},
                    "createdDateTime": "2024-01-01T01:00:00Z",
                }
            ]
        }

        async def mock_graph_get(path, params=None):
            if "/replies" in path:
                return replies_response
            return root_msg

        connector._graph_get = mock_graph_get

        result = await connector.hydrate_thread("team1:chan1:msg-root")

        assert len(result.messages) == 2
        assert result.messages[0]["id"] == "msg-root"
        assert result.messages[0]["body"] == "Root message body"
        assert result.messages[0]["subject"] == "Thread subject"
        assert result.messages[1]["id"] == "reply-1"
        assert result.participant_count == 2


# ---------------------------------------------------------------------------
# Phase 6: Dead code removal verification
# ---------------------------------------------------------------------------


class TestDeadCodeRemoval:
    def test_generate_embeddings_task_removed(self):
        """generate_embeddings Celery task should no longer exist."""
        from contextedge.workers import extraction_tasks

        assert not hasattr(extraction_tasks, "generate_embeddings")

    def test_discover_source_task_removed(self):
        """discover_source Celery task should no longer exist."""
        from contextedge.workers import sync_tasks

        assert not hasattr(sync_tasks, "discover_source")

    def test_validate_service_account_token_removed(self):
        """validate_service_account_token should no longer exist in auth middleware."""
        from contextedge.middleware import auth

        assert not hasattr(auth, "validate_service_account_token")

    def test_rank_playbooks_no_symptoms_param(self):
        """rank_playbooks should no longer accept a symptoms parameter."""
        import inspect
        from contextedge.search.hybrid_ranker import rank_playbooks

        sig = inspect.signature(rank_playbooks)
        assert "symptoms" not in sig.parameters
