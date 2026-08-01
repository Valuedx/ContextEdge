"""Jira Service Management connector via Jira REST API v3.

Supports issue retrieval via JQL, comment extraction, and webhook-based sync.
"""

import base64
from datetime import datetime
from typing import Any

import httpx

from contextedge.connectors.base import (
    BackfillResult,
    BaseConnector,
    ChangeResult,
    Checkpoint,
    CredentialStatus,
    DateRange,
    DiscoveredObject,
    HydratedThread,
    IngestionEvent,
    RateLimitConfig,
)


class JiraSmConnector(BaseConnector):
    """Connector for Jira Service Management via REST API v3."""

    def __init__(self, source_config: dict[str, Any], credentials: dict[str, Any]):
        super().__init__(source_config, credentials)
        self.base_url = credentials.get("base_url", "").rstrip("/")
        self._auth_header = self._make_auth_header()

    def _make_auth_header(self) -> str:
        email = self.credentials.get("email", "")
        token = self.credentials.get("api_token", "")
        encoded = base64.b64encode(f"{email}:{token}".encode()).decode()
        return f"Basic {encoded}"

    async def _jira_get(self, path: str, params: dict | None = None) -> dict:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self.base_url}/rest/api/3{path}",
                headers={
                    "Authorization": self._auth_header,
                    "Accept": "application/json",
                },
                params=params,
            )
            resp.raise_for_status()
            return resp.json()

    async def validate_credentials(self) -> CredentialStatus:
        try:
            await self._jira_get("/myself")
            return CredentialStatus(valid=True, message="Jira API access verified")
        except Exception as e:
            return CredentialStatus(valid=False, message=str(e))

    async def discover_objects(self) -> list[DiscoveredObject]:
        objects: list[DiscoveredObject] = []
        projects_data = await self._jira_get(
            "/project/search",
            {"maxResults": "100", "expand": "description"},
        )
        for proj in projects_data.get("values", []):
            project_type = proj.get("projectTypeKey", "")
            objects.append(
                DiscoveredObject(
                    external_id=proj["key"],
                    object_type="jira_project",
                    display_name=f"{proj['name']} ({proj['key']})",
                    metadata={
                        "project_id": proj.get("id"),
                        "project_type": project_type,
                        "is_service_desk": project_type == "service_desk",
                    },
                )
            )
        return objects

    async def backfill(
        self,
        object_id: str,
        object_type: str,
        window: DateRange,
        checkpoint: Checkpoint | None = None,
    ) -> BackfillResult:
        project_key = object_id
        events: list[IngestionEvent] = []
        start_at = checkpoint.data.get("start_at", 0) if checkpoint else 0

        start_date = window.start.strftime("%Y-%m-%d")
        end_date = window.end.strftime("%Y-%m-%d")
        jql = (
            f'project = "{project_key}" AND updated >= "{start_date}" '
            f'AND updated <= "{end_date}" ORDER BY updated ASC'
        )

        data = await self._jira_get(
            "/search",
            {
                "jql": jql,
                "maxResults": "50",
                "startAt": str(start_at),
                "fields": (
                    "summary,description,status,priority,assignee,reporter,"
                    "created,updated,issuetype,comment"
                ),
            },
        )

        for issue in data.get("issues", []):
            fields = issue.get("fields", {})
            events.append(
                IngestionEvent(
                    external_id=issue["key"],
                    source_type="jira_sm",
                    object_type="issue",
                    content={
                        "key": issue["key"],
                        "summary": fields.get("summary", ""),
                        "description": _extract_adf_text(fields.get("description")),
                        "status": fields.get("status", {}).get("name"),
                        "priority": fields.get("priority", {}).get("name"),
                        "issue_type": fields.get("issuetype", {}).get("name"),
                        "assignee": (
                            fields.get("assignee", {}).get("displayName")
                            if fields.get("assignee")
                            else None
                        ),
                        "reporter": (
                            fields.get("reporter", {}).get("displayName")
                            if fields.get("reporter")
                            else None
                        ),
                        "comment_count": fields.get("comment", {}).get("total", 0),
                    },
                    thread_id=issue["key"],
                    timestamp=_parse_jira_datetime(fields.get("updated")),
                    metadata={"project": project_key},
                )
            )

        total = data.get("total", 0)
        issues = data.get("issues", [])
        new_start_at = start_at + len(issues)
        has_more = new_start_at < total

        if has_more:
            new_checkpoint = Checkpoint(data={"start_at": new_start_at})
        else:
            latest_ts = ""
            for issue in issues:
                updated = issue.get("fields", {}).get("updated", "")
                if updated > latest_ts:
                    latest_ts = updated
            if not latest_ts:
                latest_ts = window.end.isoformat()
            new_checkpoint = Checkpoint(data={"last_updated": latest_ts})

        return BackfillResult(
            events=events,
            new_checkpoint=new_checkpoint,
            items_processed=len(events),
            has_more=has_more,
        )

    async def fetch_changes(
        self,
        object_id: str,
        object_type: str,
        checkpoint: Checkpoint,
    ) -> ChangeResult:
        project_key = object_id
        events: list[IngestionEvent] = []

        last_updated = checkpoint.data.get("last_updated", "2000-01-01")
        jql = f'project = "{project_key}" AND updated >= "{last_updated}" ORDER BY updated ASC'

        data = await self._jira_get(
            "/search",
            {
                "jql": jql,
                "maxResults": "100",
                "fields": (
                    "summary,description,status,priority,assignee,reporter,"
                    "created,updated,issuetype,comment"
                ),
            },
        )

        latest_ts = last_updated
        for issue in data.get("issues", []):
            fields = issue.get("fields", {})
            updated = fields.get("updated", "")
            if updated > latest_ts:
                latest_ts = updated

            events.append(
                IngestionEvent(
                    external_id=issue["key"],
                    source_type="jira_sm",
                    object_type="issue",
                    content={
                        "key": issue["key"],
                        "summary": fields.get("summary", ""),
                        "description": _extract_adf_text(fields.get("description")),
                        "status": fields.get("status", {}).get("name"),
                        "priority": fields.get("priority", {}).get("name"),
                        "issue_type": fields.get("issuetype", {}).get("name"),
                        "assignee": (
                            fields.get("assignee", {}).get("displayName")
                            if fields.get("assignee")
                            else None
                        ),
                        "reporter": (
                            fields.get("reporter", {}).get("displayName")
                            if fields.get("reporter")
                            else None
                        ),
                    },
                    thread_id=issue["key"],
                    timestamp=_parse_jira_datetime(updated),
                    metadata={"project": project_key},
                )
            )

        return ChangeResult(
            events=events,
            new_checkpoint=Checkpoint(data={"last_updated": latest_ts}),
            items_processed=len(events),
        )

    async def hydrate_thread(self, thread_ref: str) -> HydratedThread:
        """Fetch issue with all comments."""
        issue_key = thread_ref
        data = await self._jira_get(
            f"/issue/{issue_key}",
            {"fields": "summary,description,comment,attachment", "expand": "renderedFields"},
        )

        fields = data.get("fields", {})
        messages = []

        messages.append({
            "id": f"{issue_key}_description",
            "body": _extract_adf_text(fields.get("description")),
            "from": (
                fields.get("reporter", {}).get("displayName", "")
                if fields.get("reporter")
                else ""
            ),
            "type": "description",
            "timestamp": fields.get("created"),
        })

        for comment in fields.get("comment", {}).get("comments", []):
            messages.append({
                "id": comment["id"],
                "body": _extract_adf_text(comment.get("body")),
                "from": comment.get("author", {}).get("displayName", ""),
                "type": "comment",
                "timestamp": comment.get("created"),
            })

        participants = {m.get("from") for m in messages if m.get("from")}
        return HydratedThread(
            thread_id=thread_ref,
            messages=messages,
            participant_count=len(participants),
            metadata={"summary": fields.get("summary", "")},
        )

    def rate_limit_config(self) -> RateLimitConfig:
        return RateLimitConfig(requests_per_second=10.0, burst_size=20)


def _extract_adf_text(adf: dict | None) -> str:
    """Extract plain text from Atlassian Document Format."""
    if not adf:
        return ""
    if isinstance(adf, str):
        return adf
    parts: list[str] = []
    _walk_adf(adf, parts)
    return "\n".join(parts)


def _walk_adf(node: dict, parts: list[str]):
    if node.get("type") == "text":
        parts.append(node.get("text", ""))
    for child in node.get("content", []):
        _walk_adf(child, parts)


def _parse_jira_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
