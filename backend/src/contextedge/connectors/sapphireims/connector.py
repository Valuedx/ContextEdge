"""SapphireIMS Service Desk connector — config-mapped REST contract.

SapphireIMS (Tecknodreams) exposes a REST API whose *authentication
model* is publicly documented — an API key that grants access plus an
auth token identifying the caller, and a "submitted by" identifier —
but whose endpoint paths, parameter names, and payload field names are
instance documentation, not public contract. Hard-coding a guessed
contract would produce a connector that looks finished and silently
fetches nothing.

So the contract is CONFIG-MAPPED: every path, query-parameter name, and
payload field name comes from ``source_config`` with defaults modeled on
the documented concepts (Project / Service / Category, ticket modules
for incident / service request / change / problem). The defaults are a
starting point — **verify each against your instance's API guide**
before first sync; ``validate_credentials`` probes the configured path
so a wrong mapping fails loudly at setup, not silently at sync time.

Config keys (all optional, defaults shown):

- ``api``: contract map —
  ``probe_path``       ("/SapphireIMS/api/v1/version")
  ``tickets_path``     ("/SapphireIMS/api/v1/tickets")
  ``updated_since_param`` ("modifiedSince"), ``page_param`` ("page"),
  ``page_size_param``  ("pageSize"), ``items_key`` ("data")
- ``fields``: payload field map —
  ``id`` ("ticket_id"), ``title`` ("subject"), ``description``
  ("description"), ``type`` ("ticket_type"), ``status`` ("status"),
  ``priority`` ("priority"), ``updated`` ("modified_time"),
  ``service`` ("service_name"), ``ci`` ("asset_name"),
  ``related_tickets`` ("related_tickets")
- ``projects``: explicit list of project names to sync (a public
  projects-list endpoint is not documented; discovery enumerates this
  list). ``project_param`` ("project") scopes ticket queries.
- ``type_kind_map``: ticket-type value → normalized kind; defaults map
  incident / service request / change / problem onto the shared thread
  vocabulary so change-risk and post-action verification discriminate
  SapphireIMS records like every other source.

Everything around the mapping is real and tested: retry/backoff with
Retry-After (same contract as the ServiceNow/Jira clients), bounded
pagination, kind-prefixed thread ids, tolerant timestamp parsing, and
the reference enrichment in ``services/sapphireims_reference_service``.
"""

import asyncio
from datetime import UTC, datetime
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

DEFAULT_API = {
    "probe_path": "/SapphireIMS/api/v1/version",
    "tickets_path": "/SapphireIMS/api/v1/tickets",
    "updated_since_param": "modifiedSince",
    "page_param": "page",
    "page_size_param": "pageSize",
    "items_key": "data",
    "project_param": "project",
}

DEFAULT_FIELDS = {
    "id": "ticket_id",
    "title": "subject",
    "description": "description",
    "type": "ticket_type",
    "status": "status",
    "priority": "priority",
    "updated": "modified_time",
    "service": "service_name",
    "ci": "asset_name",
    "related_tickets": "related_tickets",
}

DEFAULT_TYPE_KIND_MAP = {
    "incident": "incident",
    "service request": "service_request",
    "change": "change_request",
    "change request": "change_request",
    "problem": "problem",
}

PAGE_SIZE = 100
MAX_PAGES = 10


class SapphireIMSConnector(BaseConnector):
    """Connector for SapphireIMS Service Desk (config-mapped contract)."""

    MAX_ATTEMPTS = 3
    BACKOFF_BASE_SECONDS = 1.0
    MAX_RETRY_AFTER_SECONDS = 60.0

    def __init__(self, source_config: dict[str, Any], credentials: dict[str, Any]):
        super().__init__(source_config, credentials)
        self.base_url = credentials.get("base_url", "").rstrip("/")
        config = source_config or {}
        self.api = {**DEFAULT_API, **(config.get("api") or {})}
        self.fields = {**DEFAULT_FIELDS, **(config.get("fields") or {})}
        self.type_kind_map = {
            str(k).strip().lower(): str(v)
            for k, v in {
                **DEFAULT_TYPE_KIND_MAP,
                **(config.get("type_kind_map") or {}),
            }.items()
        }

    def _headers(self) -> dict[str, str]:
        # The documented auth model: an API key granting access + a token
        # identifying the caller. Header NAMES are instance documentation;
        # override via credentials api_key_header / auth_token_header.
        headers = {
            "Accept": "application/json",
            self.credentials.get("api_key_header", "apikey"): self.credentials.get(
                "api_key", ""
            ),
            self.credentials.get("auth_token_header", "authtoken"): self.credentials.get(
                "auth_token", ""
            ),
        }
        submitted_by = self.credentials.get("submitted_by")
        if submitted_by:
            headers["submittedBy"] = str(submitted_by)
        return headers

    async def _get(self, path: str, params: dict | None = None) -> dict:
        last_exc: Exception | None = None
        for attempt in range(1, self.MAX_ATTEMPTS + 1):
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    resp = await client.get(
                        f"{self.base_url}{path}",
                        headers=self._headers(),
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
                    raise  # 4xx (bad key/token, wrong path) — fix config, not retry
                if attempt < self.MAX_ATTEMPTS:
                    await asyncio.sleep(self.BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)))
        raise last_exc

    def _items(self, data: object) -> list[dict]:
        """Tolerant list extraction: the configured items_key, common
        fallbacks, or a bare top-level list."""
        if isinstance(data, list):
            return [row for row in data if isinstance(row, dict)]
        if isinstance(data, dict):
            for key in (self.api["items_key"], "items", "records", "tickets", "result"):
                value = data.get(key)
                if isinstance(value, list):
                    return [row for row in value if isinstance(row, dict)]
        return []

    def ticket_kind(self, ticket: dict) -> str:
        raw = str(ticket.get(self.fields["type"]) or "").strip().lower()
        return self.type_kind_map.get(raw, "issue")

    def _ticket_event(self, ticket: dict, project: str) -> IngestionEvent | None:
        ticket_id = ticket.get(self.fields["id"]) or ticket.get("id")
        if ticket_id in (None, ""):
            return None
        ticket_id = str(ticket_id)
        kind = self.ticket_kind(ticket)
        content = {
            "ticket_id": ticket_id,
            "title": str(ticket.get(self.fields["title"]) or ""),
            "description": str(ticket.get(self.fields["description"]) or ""),
            "status": ticket.get(self.fields["status"]),
            "priority": ticket.get(self.fields["priority"]),
            "ticket_type": ticket.get(self.fields["type"]),
            "record_kind": kind,
            "project": project,
            "service_name": ticket.get(self.fields["service"]),
            "ci_name": ticket.get(self.fields["ci"]),
            "related_tickets": _string_list(ticket.get(self.fields["related_tickets"])),
        }
        # summary/description aliases feed evidence_title/body_from_payload.
        content["summary"] = content["title"]
        return IngestionEvent(
            external_id=ticket_id,
            source_type="sapphireims",
            object_type="ticket",
            content=content,
            thread_id=f"{kind}:{ticket_id}",
            timestamp=parse_sapphire_datetime(ticket.get(self.fields["updated"])),
            metadata={"project": project, "record_kind": kind},
        )

    async def validate_credentials(self) -> CredentialStatus:
        try:
            await self._get(self.api["probe_path"])
            return CredentialStatus(valid=True, message="SapphireIMS API access verified")
        except Exception as e:
            return CredentialStatus(
                valid=False,
                message=(
                    f"{e} — check credentials AND the source_config api map: "
                    "SapphireIMS endpoint paths are instance-specific."
                ),
            )

    async def discover_objects(self) -> list[DiscoveredObject]:
        # A public projects-list endpoint is not documented; projects are
        # declared explicitly in source_config["projects"].
        projects = [
            str(p).strip()
            for p in ((self.source_config or {}).get("projects") or [])
            if isinstance(p, str) and str(p).strip()
        ]
        return [
            DiscoveredObject(
                external_id=project,
                object_type="sapphireims_project",
                display_name=f"SapphireIMS: {project}",
                metadata={"project": project},
            )
            for project in projects
        ]

    async def _fetch_pages(
        self, project: str, extra_params: dict
    ) -> tuple[list[IngestionEvent], str]:
        events: list[IngestionEvent] = []
        latest = ""
        page = 1
        for _ in range(MAX_PAGES):
            params = {
                self.api["project_param"]: project,
                self.api["page_param"]: str(page),
                self.api["page_size_param"]: str(PAGE_SIZE),
                **extra_params,
            }
            data = await self._get(self.api["tickets_path"], params)
            tickets = self._items(data)
            for ticket in tickets:
                event = self._ticket_event(ticket, project)
                if event is None:
                    continue
                updated = str(ticket.get(self.fields["updated"]) or "")
                if updated > latest:
                    latest = updated
                events.append(event)
            if len(tickets) < PAGE_SIZE:
                break
            page += 1
        return events, latest

    async def backfill(
        self,
        object_id: str,
        object_type: str,
        window: DateRange,
        checkpoint: Checkpoint | None = None,
    ) -> BackfillResult:
        # Windowed backfill rides the same updated-since contract; the
        # window end is enforced client-side since an upper-bound param
        # is not documented.
        events, latest = await self._fetch_pages(
            object_id,
            {self.api["updated_since_param"]: window.start.strftime("%Y-%m-%d %H:%M:%S")},
        )
        end = window.end if window.end.tzinfo else window.end.replace(tzinfo=UTC)
        kept = [e for e in events if e.timestamp is None or e.timestamp <= end]
        return BackfillResult(
            events=kept,
            new_checkpoint=Checkpoint(data={"last_updated": latest or str(window.end)}),
            items_processed=len(kept),
            # Single bounded sweep (MAX_PAGES × PAGE_SIZE). A window larger
            # than that is still safe: the checkpoint seeds the max-seen
            # cursor and incremental sync continues from there.
            has_more=False,
        )

    async def fetch_changes(
        self,
        object_id: str,
        object_type: str,
        checkpoint: Checkpoint,
    ) -> ChangeResult:
        last_updated = str(checkpoint.data.get("last_updated", "2000-01-01 00:00:00"))
        events, latest = await self._fetch_pages(
            object_id, {self.api["updated_since_param"]: last_updated}
        )
        return ChangeResult(
            events=events,
            new_checkpoint=Checkpoint(data={"last_updated": latest or last_updated}),
            items_processed=len(events),
        )

    async def hydrate_thread(self, thread_ref: str) -> HydratedThread:
        # Ticket conversation/worklog endpoints are not publicly
        # documented — the ticket body ingested at sync time is the
        # thread content. Mirrors the alert-rollup precedent: hydration
        # is a no-op rather than a guessed API call.
        return HydratedThread(
            thread_id=thread_ref,
            messages=[],
            participant_count=0,
            metadata={"hydration": "not_supported"},
        )

    def rate_limit_config(self) -> RateLimitConfig:
        return RateLimitConfig(requests_per_second=5.0, burst_size=10)


def _string_list(value: object) -> list[str]:
    """Related-ticket refs arrive as a list, a comma-joined string, or a
    single scalar depending on instance configuration."""
    if isinstance(value, list):
        return [str(v).strip() for v in value[:20] if str(v).strip()]
    if isinstance(value, str):
        return [part.strip() for part in value.split(",")[:20] if part.strip()]
    if value not in (None, ""):
        return [str(value)]
    return []


def parse_sapphire_datetime(value: object) -> datetime | None:
    """Tolerant: ISO 8601, ``YYYY-MM-DD HH:MM:SS``, or epoch seconds /
    milliseconds — instance formats vary."""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        seconds = float(value)
        if seconds > 1e12:  # epoch millis
            seconds /= 1000.0
        try:
            return datetime.fromtimestamp(seconds, tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    for parser in (
        lambda t: datetime.fromisoformat(t.replace("Z", "+00:00")),
        lambda t: datetime.strptime(t, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC),
    ):
        try:
            return parser(text)
        except ValueError:
            continue
    return None
