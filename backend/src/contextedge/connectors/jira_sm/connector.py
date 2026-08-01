"""Jira Service Management connector via Jira REST API v3.

Supports issue retrieval via JQL, comment extraction, and webhook-based
sync. Since the 2026-08 reference-edges work, each issue event also
carries the relationship data JSM exposes universally — issue links
(with the linked issue's type, so "is caused by a Change" is knowable
without a second fetch), the parent issue, components, labels, and
resolution — plus, when ``source_config["service_field_id"]`` names the
instance's JSM affected-services custom field, the linked services.
``services/jira_reference_service.py`` turns these into case links,
typed graph edges, and entities, mirroring the ServiceNow Phase 1
enrichment.

Thread ids are kind-prefixed (``incident:PROJ-123``) so downstream
record-kind discrimination (change risk, post-action verification)
works for Jira exactly as it does for ServiceNow tables. Issues
ingested before this change live in bare-key threads; their next
re-delivery starts a fresh kind-prefixed thread (old threads remain
readable — disclosed in KNOWN_GAPS).
"""

import asyncio
import base64
from datetime import datetime, timedelta
from typing import Any

import httpx
import structlog

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

ISSUE_FIELDS = (
    "summary,description,status,priority,assignee,reporter,"
    "created,updated,issuetype,comment,issuelinks,parent,"
    "components,labels,resolution"
)

# issuetype name (lowercased) → normalized record kind. The kinds match
# the ServiceNow thread-prefix vocabulary so downstream discrimination
# (change_risk_service, execution_verification_service) treats a Jira
# Change like a change_request table row. Unmapped types → "issue".
ISSUE_KIND_MAP = {
    "incident": "incident",
    "[system] incident": "incident",
    "problem": "problem",
    "[system] problem": "problem",
    "change": "change_request",
    "[system] change": "change_request",
    "service request": "service_request",
    "[system] service request": "service_request",
    "service request with approvals": "service_request",
}

# Incremental sync: page size × max pages bounds one invocation. The
# single-page fetch this replaces silently dropped everything past the
# first 100 updates in a tick.
INCREMENTAL_PAGE_SIZE = 100
INCREMENTAL_MAX_PAGES = 10


def issue_kind(issue_type_name: str | None) -> str:
    return ISSUE_KIND_MAP.get((issue_type_name or "").strip().lower(), "issue")


def _slim_issue_links(fields: dict) -> list[dict]:
    """Reduce issuelinks to what reference extraction needs: the link
    type's directional name, the linked issue's key, and its type (the
    v3 response embeds linked-issue fields, so no second fetch)."""
    links: list[dict] = []
    for link in fields.get("issuelinks") or []:
        link_type = link.get("type") or {}
        for direction, description_key in (("inward", "inward"), ("outward", "outward")):
            linked = link.get(f"{direction}Issue")
            if not linked:
                continue
            linked_fields = linked.get("fields") or {}
            links.append(
                {
                    "direction": direction,
                    "description": link_type.get(description_key),
                    "type_name": link_type.get("name"),
                    "key": linked.get("key"),
                    "issue_type": (linked_fields.get("issuetype") or {}).get("name"),
                }
            )
    return links


logger = structlog.get_logger()


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

    # source_config customfield keys (D1): every JSM instance maps these
    # differently, so they are config, validated to the customfield_
    # namespace — a typo degrades to "field absent", never a bad query.
    _CUSTOMFIELD_KEYS = (
        "service_field_id",
        "request_type_field_id",
        "change_start_field_id",
        "change_end_field_id",
    )

    def _configured_customfields(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for key in self._CUSTOMFIELD_KEYS:
            value = (self.source_config or {}).get(key)
            if value and str(value).startswith("customfield_"):
                out[key] = str(value)
        return out

    def _issue_fields_param(self) -> str:
        extra = ",".join(sorted(set(self._configured_customfields().values())))
        return f"{ISSUE_FIELDS},{extra}" if extra else ISSUE_FIELDS

    # Retry transient failures (429 / 5xx / transport errors) with capped
    # exponential backoff, honoring Retry-After — Atlassian rate-limits
    # aggressively, and one 429 previously failed the whole sync task.
    # Same contract as the ServiceNow connector's _snow_get.
    MAX_ATTEMPTS = 3
    BACKOFF_BASE_SECONDS = 1.0
    MAX_RETRY_AFTER_SECONDS = 60.0

    async def _jira_get(self, path: str, params: dict | None = None) -> dict:
        last_exc: Exception | None = None
        for attempt in range(1, self.MAX_ATTEMPTS + 1):
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    resp = await client.get(
                        f"{self.base_url}/rest/api/3{path}",
                        headers={
                            "Authorization": self._auth_header,
                            "Accept": "application/json",
                        },
                        params=params,
                    )
                if resp.status_code == 429 or resp.status_code >= 500:
                    retry_after = resp.headers.get("Retry-After")
                    try:
                        delay = min(
                            float(retry_after) if retry_after else
                            self.BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)),
                            self.MAX_RETRY_AFTER_SECONDS,
                        )
                    except ValueError:
                        delay = self.BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))
                    if attempt < self.MAX_ATTEMPTS:
                        await asyncio.sleep(delay)
                        continue
                resp.raise_for_status()
                return resp.json()
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                last_exc = exc
                if (
                    isinstance(exc, httpx.HTTPStatusError)
                    and exc.response.status_code < 500
                    and exc.response.status_code != 429
                ):
                    raise  # 4xx (auth, bad JQL) will not improve on retry
                if attempt < self.MAX_ATTEMPTS:
                    await asyncio.sleep(self.BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)))
        raise last_exc  # exhausted retries

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

    def _issue_event(self, issue: dict, project_key: str) -> IngestionEvent:
        fields = issue.get("fields", {})
        kind = issue_kind((fields.get("issuetype") or {}).get("name"))
        content = {
            "key": issue["key"],
            "summary": fields.get("summary", ""),
            "description": _extract_adf_text(fields.get("description")),
            "status": (fields.get("status") or {}).get("name"),
            "priority": (fields.get("priority") or {}).get("name"),
            "issue_type": (fields.get("issuetype") or {}).get("name"),
            "record_kind": kind,
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
            "comment_count": (fields.get("comment") or {}).get("total", 0),
            "labels": list(fields.get("labels") or [])[:20],
            "resolution": (fields.get("resolution") or {}).get("name"),
            "issue_links": _slim_issue_links(fields),
            "components": [
                {"id": c.get("id"), "name": c.get("name")}
                for c in (fields.get("components") or [])[:10]
                if isinstance(c, dict) and c.get("name")
            ],
        }
        parent = fields.get("parent")
        if parent and parent.get("key"):
            content["parent_key"] = parent["key"]
            content["parent_issue_type"] = (
                (parent.get("fields") or {}).get("issuetype") or {}
            ).get("name")
        configured = self._configured_customfields()
        request_type_field = configured.get("request_type_field_id")
        if request_type_field and fields.get(request_type_field) is not None:
            raw_rt = fields[request_type_field]
            # JSM shapes vary: {"requestType": {"name": ...}}, {"name": ...},
            # or a plain string. Tolerant, never guessed.
            name = None
            if isinstance(raw_rt, dict):
                inner = raw_rt.get("requestType")
                if isinstance(inner, dict):
                    name = inner.get("name")
                name = name or raw_rt.get("name")
            elif isinstance(raw_rt, str):
                name = raw_rt
            if name:
                content["request_type"] = str(name)[:120]
        window_start = configured.get("change_start_field_id")
        window_end = configured.get("change_end_field_id")
        start_value = fields.get(window_start) if window_start else None
        end_value = fields.get(window_end) if window_end else None
        if start_value or end_value:
            content["change_window"] = {
                "start": str(start_value) if start_value else None,
                "end": str(end_value) if end_value else None,
            }
        service_field = (self.source_config or {}).get("service_field_id")
        if service_field and service_field in fields:
            raw_services = fields.get(service_field) or []
            if isinstance(raw_services, list):
                content["affected_services"] = [
                    {"id": s.get("id"), "name": s.get("name")}
                    for s in raw_services[:10]
                    if isinstance(s, dict) and s.get("name")
                ]
        return IngestionEvent(
            external_id=issue["key"],
            source_type="jira_sm",
            object_type="issue",
            content=content,
            # Kind-prefixed so record-kind discrimination works downstream.
            thread_id=f"{kind}:{issue['key']}",
            timestamp=_parse_jira_datetime(fields.get("updated")),
            metadata={"project": project_key, "record_kind": kind},
        )

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
                "fields": self._issue_fields_param(),
            },
        )

        for issue in data.get("issues", []):
            events.append(self._issue_event(issue, project_key))

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
        # JQL datetime comparisons accept "yyyy-MM-dd HH:mm" and are
        # evaluated in the API account's timezone, while the stored
        # ``updated`` stamps carry a UTC offset. Rewind the cursor by an
        # overlap window so timezone/format slop re-delivers (downstream
        # dedup absorbs it) instead of silently skipping. Recommendation:
        # set the integration account's timezone to UTC. (The full-ISO
        # cursor this replaces was not even valid JQL — incremental sync
        # broke on the first non-default checkpoint.)
        cursor = _jql_cursor(str(last_updated))
        jql = f'project = "{project_key}" AND updated >= "{cursor}" ORDER BY updated ASC'

        # Bounded pagination: the single-page fetch this replaces silently
        # dropped everything past the first page in a busy tick.
        latest_ts = last_updated
        start_at = 0
        # Page-order guard (D1): offset pagination over a mutating result
        # set can shift rows between pages. Within one tick the ASC order
        # must be monotone across pages — a page whose first row sorts
        # BEFORE the previous page's last row means the snapshot moved
        # under us. Stop paging and clamp the cursor to the last
        # consistent point; the rewind + dedup re-deliver the rest next
        # tick instead of silently skipping.
        prev_page_max = ""
        for _page in range(INCREMENTAL_MAX_PAGES):
            data = await self._jira_get(
                "/search",
                {
                    "jql": jql,
                    "maxResults": str(INCREMENTAL_PAGE_SIZE),
                    "startAt": str(start_at),
                    "fields": self._issue_fields_param(),
                },
            )
            issues = data.get("issues", [])
            page_stamps = [
                issue.get("fields", {}).get("updated", "") for issue in issues
            ]
            page_min = min(filter(None, page_stamps), default="")
            if issues and prev_page_max and page_min and page_min < prev_page_max:
                logger.warning(
                    "jira_sync.page_order_mutation",
                    project=project_key,
                    clamped_cursor=prev_page_max,
                )
                latest_ts = min(latest_ts, prev_page_max) if latest_ts else prev_page_max
                break
            for issue in issues:
                updated = issue.get("fields", {}).get("updated", "")
                if updated > latest_ts:
                    latest_ts = updated
                events.append(self._issue_event(issue, project_key))
            prev_page_max = max(filter(None, page_stamps), default=prev_page_max)
            start_at += len(issues)
            if len(issues) < INCREMENTAL_PAGE_SIZE:
                break

        return ChangeResult(
            events=events,
            new_checkpoint=Checkpoint(data={"last_updated": latest_ts}),
            items_processed=len(events),
        )

    async def hydrate_thread(self, thread_ref: str) -> HydratedThread:
        """Fetch issue with all comments. Thread refs are kind-prefixed
        (``incident:PROJ-123``) since the reference-edges work; bare keys
        from pre-existing threads still resolve."""
        issue_key = thread_ref.rsplit(":", 1)[-1]
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


CURSOR_OVERLAP_MINUTES = 30


def _jql_cursor(last_updated: str) -> str:
    """ISO-ish checkpoint stamp → JQL-safe minute cursor, rewound by the
    overlap window. Falls back to a plain trim for unparseable stamps."""
    try:
        parsed = datetime.fromisoformat(last_updated.replace("Z", "+00:00"))
        rewound = parsed - timedelta(minutes=CURSOR_OVERLAP_MINUTES)
        return rewound.strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return last_updated[:16].replace("T", " ")


def _parse_jira_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
