"""ManageEngine ServiceDesk Plus Connector for ContextEdge."""

import asyncio
import json
import logging
from datetime import datetime, timedelta
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

from .models import METicket, MEWorklog

logger = logging.getLogger(__name__)


class MEError(Exception):
    """Base class for ManageEngine connector errors."""

    pass


class MERateLimitError(MEError):
    """Raised when ManageEngine rate limit (4001) is hit and retries exhausted."""

    pass


class ManageEngineConnector(BaseConnector):
    """ManageEngine ServiceDesk Plus V3 API connector for ContextEdge."""

    def __init__(
        self, source_config: dict[str, Any], credentials: dict[str, Any]
    ):
        super().__init__(source_config, credentials)
        self.base_url = credentials.get("base_url", "").rstrip("/")
        self.api_key = credentials.get("api_key", "")
        self.api_base = f"{self.base_url}/api/v3"
        self.is_mock = "mock" in self.base_url.lower()

        self.headers = {
            "Accept": "application/vnd.manageengine.sdp.v3+json",
            "Content-Type": "application/x-www-form-urlencoded",
            "TECHNICIAN_KEY": self.api_key,
        }

    async def validate_credentials(self) -> CredentialStatus:
        """Test connection to ManageEngine."""
        try:
            input_data = {"list_info": {"row_count": 1, "start_index": 1}}
            await self._request(
                "GET", "/requests", params={"input_data": json.dumps(input_data)}
            )
            return CredentialStatus(valid=True, message="Connection successful")
        except MERateLimitError:
            return CredentialStatus(valid=False, message="Rate limit exceeded")
        except Exception as e:
            return CredentialStatus(valid=False, message=f"Connection failed: {str(e)}")

    async def discover_objects(self) -> list[DiscoveredObject]:
        """Discover available ticket queues/categories in ManageEngine."""
        objects = []
        try:
            # Create a generic "All Tickets" object
            objects.append(
                DiscoveredObject(
                    external_id="all_tickets",
                    object_type="ticket_queue",
                    display_name="All Tickets",
                    object_path="/requests",
                    metadata={"description": "All service desk tickets"},
                )
            )

            # Try to fetch available categories
            try:
                result = await self._request(
                    "GET", "/sdp_master_objects/categories"
                )
                for cat in result.get("categories", []):
                    objects.append(
                        DiscoveredObject(
                            external_id=f"category_{cat.get('id', '')}",
                            object_type="ticket_queue",
                            display_name=f"Category: {cat.get('name', '')}",
                            metadata={
                                "category_id": cat.get("id"),
                                "category_name": cat.get("name"),
                            },
                        )
                    )
            except Exception as e:
                logger.warning("Failed to fetch categories", error=str(e))

            logger.info("Discovered ManageEngine objects", count=len(objects))
            return objects
        except Exception as e:
            logger.error("Failed to discover objects", error=str(e))
            raise

    async def backfill(
        self,
        object_id: str,
        object_type: str,
        window: DateRange,
        checkpoint: Checkpoint | None = None,
    ) -> BackfillResult:
        """Backfill tickets within a date range."""
        try:
            tickets = await self._fetch_tickets(
                since=window.start,
                until=window.end,
                start_index=checkpoint.data.get("start_index", 1)
                if checkpoint
                else 1,
            )

            events = []
            for ticket in tickets:
                # Fetch worklogs and notes
                worklogs = await self._fetch_ticket_worklogs(ticket.id)
                notes = await self._fetch_ticket_notes(ticket.id)

                # Create main ticket event
                event = IngestionEvent(
                    external_id=ticket.id,
                    source_type="manageengine",
                    object_type="ticket",
                    content={
                        "id": ticket.id,
                        "ticket_number": ticket.ticket_number,
                        "subject": ticket.subject,
                        "description": ticket.description,
                        "category": ticket.category,
                        "priority": ticket.priority,
                        "status": ticket.status,
                        "group_name": ticket.group_name,
                        "assignee_name": ticket.assignee_name,
                        "created_time": ticket.created_time.isoformat()
                        if ticket.created_time
                        else None,
                        "closed_time": ticket.closed_time.isoformat()
                        if ticket.closed_time
                        else None,
                        "resolution": ticket.resolution,
                        "worklogs": [
                            {
                                "id": wl.id,
                                "description": wl.description,
                                "technician_name": wl.technician_name,
                                "created_time": wl.created_time.isoformat()
                                if wl.created_time
                                else None,
                            }
                            for wl in worklogs
                        ],
                        "notes": [
                            {
                                "id": n.id,
                                "description": n.description,
                                "technician_name": n.technician_name,
                                "created_time": n.created_time.isoformat()
                                if n.created_time
                                else None,
                            }
                            for n in notes
                        ],
                    },
                    timestamp=ticket.created_time,
                )
                events.append(event)

            # Create new checkpoint for pagination
            new_checkpoint = Checkpoint(
                data={
                    "start_index": 1 + len(tickets),
                    "last_sync": datetime.utcnow().isoformat(),
                },
                captured_at=datetime.utcnow(),
            )

            return BackfillResult(
                events=events,
                new_checkpoint=new_checkpoint,
                items_processed=len(tickets),
                has_more=False,
            )
        except Exception as e:
            logger.error("Backfill failed", error=str(e))
            raise

    async def fetch_changes(
        self,
        object_id: str,
        object_type: str,
        checkpoint: Checkpoint,
    ) -> ChangeResult:
        """Fetch changes since last checkpoint."""
        try:
            last_sync = datetime.fromisoformat(
                checkpoint.data.get("last_sync", datetime.utcnow().isoformat())
            )
            now = datetime.utcnow()

            tickets = await self._fetch_tickets(since=last_sync, until=now)

            events = []
            for ticket in tickets:
                worklogs = await self._fetch_ticket_worklogs(ticket.id)
                notes = await self._fetch_ticket_notes(ticket.id)

                event = IngestionEvent(
                    external_id=ticket.id,
                    source_type="manageengine",
                    object_type="ticket",
                    content={
                        "id": ticket.id,
                        "ticket_number": ticket.ticket_number,
                        "subject": ticket.subject,
                        "description": ticket.description,
                        "category": ticket.category,
                        "priority": ticket.priority,
                        "status": ticket.status,
                        "group_name": ticket.group_name,
                        "assignee_name": ticket.assignee_name,
                        "created_time": ticket.created_time.isoformat()
                        if ticket.created_time
                        else None,
                        "closed_time": ticket.closed_time.isoformat()
                        if ticket.closed_time
                        else None,
                        "resolution": ticket.resolution,
                        "worklogs": [
                            {
                                "id": wl.id,
                                "description": wl.description,
                                "technician_name": wl.technician_name,
                                "created_time": wl.created_time.isoformat()
                                if wl.created_time
                                else None,
                            }
                            for wl in worklogs
                        ],
                        "notes": [
                            {
                                "id": n.id,
                                "description": n.description,
                                "technician_name": n.technician_name,
                                "created_time": n.created_time.isoformat()
                                if n.created_time
                                else None,
                            }
                            for n in notes
                        ],
                    },
                    timestamp=ticket.created_time,
                )
                events.append(event)

            new_checkpoint = Checkpoint(
                data={"last_sync": now.isoformat()},
                captured_at=now,
            )

            return ChangeResult(
                events=events,
                new_checkpoint=new_checkpoint,
                items_processed=len(tickets),
            )
        except Exception as e:
            logger.error("Fetch changes failed", error=str(e))
            raise

    async def hydrate_thread(self, thread_ref: str) -> HydratedThread:
        """Hydrate a ticket thread with full details and comments."""
        try:
            ticket = await self._fetch_ticket(thread_ref)
            if not ticket:
                return HydratedThread(thread_id=thread_ref, messages=[])

            worklogs = await self._fetch_ticket_worklogs(thread_ref)
            notes = await self._fetch_ticket_notes(thread_ref)

            messages = [
                {
                    "id": ticket.id,
                    "type": "ticket",
                    "subject": ticket.subject,
                    "description": ticket.description,
                    "created_by": ticket.assignee_name,
                    "created_at": ticket.created_time.isoformat()
                    if ticket.created_time
                    else None,
                }
            ]

            for worklog in worklogs:
                messages.append(
                    {
                        "id": worklog.id,
                        "type": "worklog",
                        "description": worklog.description,
                        "created_by": worklog.technician_name,
                        "created_at": worklog.created_time.isoformat()
                        if worklog.created_time
                        else None,
                    }
                )

            for note in notes:
                messages.append(
                    {
                        "id": note.id,
                        "type": "note",
                        "description": note.description,
                        "created_by": note.technician_name,
                        "created_at": note.created_time.isoformat()
                        if note.created_time
                        else None,
                    }
                )

            return HydratedThread(
                thread_id=thread_ref,
                messages=messages,
                participant_count=len(
                    set(m.get("created_by") for m in messages if m.get("created_by"))
                ),
            )
        except Exception as e:
            logger.error("Hydrate thread failed", error=str(e))
            raise

    def rate_limit_config(self) -> RateLimitConfig:
        """ManageEngine SDP has aggressive rate limits."""
        return RateLimitConfig(requests_per_second=5.0, burst_size=10)

    # Private helper methods

    async def _request(
        self,
        method: str,
        endpoint: str,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
    ) -> dict:
        """Make API request to ManageEngine."""
        if not endpoint.startswith("/"):
            endpoint = f"/{endpoint}"
        url = f"{self.api_base}{endpoint}"

        if self.is_mock:
            return self._mock_response(endpoint)

        headers = self.headers.copy()
        params = params or {}

        # Move TECHNICIAN_KEY to params for GET requests
        if method == "GET":
            params["TECHNICIAN_KEY"] = self.api_key
            if "TECHNICIAN_KEY" in headers:
                del headers["TECHNICIAN_KEY"]
            if "Content-Type" in headers:
                del headers["Content-Type"]
            headers["Accept"] = "application/json"

        async with httpx.AsyncClient(timeout=15.0, verify=False) as client:
            max_retries = 3
            retry_delay = 2.0

            for attempt in range(max_retries):
                try:
                    response = await client.request(
                        method=method,
                        url=url,
                        headers=headers,
                        params=params,
                        data=data,
                    )

                    if response.status_code == 400:
                        try:
                            error_data = response.json()
                            resp_status = error_data.get("response_status", {})
                            status_code = resp_status.get("status_code")
                            messages = resp_status.get("messages", [])
                            is_throttled = status_code == 4001 or any(
                                m.get("status_code") == 4001 for m in messages
                            )

                            if is_throttled and attempt < max_retries - 1:
                                wait_time = retry_delay * (attempt + 1) * 2
                                logger.warning(
                                    "ME rate limit hit, retrying",
                                    url=url,
                                    attempt=attempt + 1,
                                )
                                await asyncio.sleep(wait_time)
                                continue
                            elif is_throttled:
                                logger.error("ME rate limit exhausted", url=url)
                                raise MERateLimitError(
                                    f"ManageEngine rate limit exceeded on {url}"
                                )
                        except MERateLimitError:
                            raise
                        except Exception:
                            pass

                    response.raise_for_status()
                    return response.json()
                except httpx.HTTPStatusError as e:
                    if attempt == max_retries - 1:
                        logger.error(
                            "ME API error",
                            status_code=e.response.status_code,
                            url=url,
                        )
                        raise
                    if e.response.status_code >= 500:
                        await asyncio.sleep(retry_delay * (attempt + 1))
                        continue
                    raise
                except Exception as e:
                    if isinstance(e, MERateLimitError):
                        raise
                    if attempt == max_retries - 1:
                        logger.error(
                            "ME request failed", url=url, error=str(e)
                        )
                        raise
                    await asyncio.sleep(retry_delay * (attempt + 1))

    async def _fetch_tickets(
        self,
        since: datetime,
        until: datetime | None = None,
        row_count: int = 100,
        start_index: int = 1,
    ) -> list[METicket]:
        """Fetch tickets within date range."""

        def to_epoch_ms(dt: datetime) -> int:
            return int(dt.timestamp() * 1000)

        criteria = [
            {
                "field": "status.name",
                "condition": "is",
                "values": ["Closed", "Resolved"],
            },
            {
                "field": "created_time",
                "condition": "greater than",
                "values": [to_epoch_ms(since)],
            },
        ]

        if until:
            criteria.append(
                {
                    "field": "created_time",
                    "condition": "lesser than",
                    "values": [to_epoch_ms(until)],
                }
            )

        row_count = min(row_count, 100)

        list_info = {
            "row_count": row_count,
            "start_index": start_index,
            "sort_field": "created_time",
            "sort_order": "desc",
            "search_criteria": criteria,
        }

        try:
            result = await self._request(
                "GET",
                "/requests",
                params={"input_data": json.dumps({"list_info": list_info})},
            )
        except Exception as e:
            logger.warning(
                "Search-based sync failed, trying fallback", error=str(e)
            )
            # Fallback: Just get recent tickets without complex search
            fallback_info = {"row_count": row_count, "start_index": start_index}
            result = await self._request(
                "GET",
                "/requests",
                params={"input_data": json.dumps({"list_info": fallback_info})},
            )

        tickets = []
        for req in result.get("requests", []):
            ticket = self._parse_ticket(req)
            tickets.append(ticket)

        logger.info(
            "Fetched tickets from ManageEngine",
            count=len(tickets),
            since=since.isoformat(),
        )
        return tickets

    async def _fetch_ticket(self, ticket_id: str) -> METicket | None:
        """Fetch full details for a single ticket."""
        try:
            result = await self._request("GET", f"/requests/{ticket_id}")
            req_data = result.get("request")
            if not req_data:
                logger.warning("No request data found", ticket_id=ticket_id)
                return None
            return self._parse_ticket(req_data)
        except Exception as e:
            logger.error(
                "Failed to fetch ticket details", ticket_id=ticket_id, error=str(e)
            )
            return None

    async def _fetch_ticket_worklogs(self, ticket_id: str) -> list[MEWorklog]:
        """Fetch worklogs for a ticket."""
        try:
            result = await self._request("GET", f"/requests/{ticket_id}/worklogs")
            worklogs = []
            for wl in result.get("worklogs", []):
                worklogs.append(
                    MEWorklog(
                        id=str(wl.get("id", "")),
                        ticket_id=ticket_id,
                        description=wl.get("description", ""),
                        technician_name=wl.get("owner", {}).get("name")
                        if wl.get("owner")
                        else None,
                        created_time=self._parse_datetime(
                            wl.get("recorded_time") or wl.get("created_time")
                        ),
                        raw_json=wl,
                    )
                )
            return worklogs
        except Exception as e:
            logger.warning(
                "Failed to fetch worklogs", ticket_id=ticket_id, error=str(e)
            )
            return []

    async def _fetch_ticket_notes(self, ticket_id: str) -> list[MEWorklog]:
        """Fetch notes for a ticket."""
        try:
            result = await self._request("GET", f"/requests/{ticket_id}/notes")
            notes = []
            for note in result.get("notes", []):
                content = (
                    note.get("notetext")
                    or note.get("text")
                    or note.get("description")
                    or ""
                )
                notes.append(
                    MEWorklog(
                        id=str(note.get("id", "")),
                        ticket_id=ticket_id,
                        description=content,
                        technician_name=note.get("added_by", {}).get("name")
                        if note.get("added_by")
                        else None,
                        created_time=self._parse_datetime(
                            note.get("recorded_time") or note.get("added_time")
                        ),
                        raw_json=note,
                    )
                )
            return notes
        except Exception as e:
            logger.warning(
                "Failed to fetch notes", ticket_id=ticket_id, error=str(e)
            )
            return []

    def _parse_ticket(self, data: dict) -> METicket:
        """Parse ManageEngine ticket response."""
        ticket_number = str(data.get("display_id") or data.get("id", ""))
        description = data.get("description") or data.get("short_description", "")

        return METicket(
            id=str(data.get("id", "")),
            ticket_number=ticket_number,
            subject=data.get("subject", ""),
            description=description,
            short_description=data.get("short_description", ""),
            category=data.get("category", {}).get("name")
            if data.get("category")
            else None,
            subcategory=data.get("subcategory", {}).get("name")
            if data.get("subcategory")
            else None,
            priority=data.get("priority", {}).get("name")
            if data.get("priority")
            else None,
            impact=data.get("impact", {}).get("name")
            if data.get("impact")
            else None,
            urgency=data.get("urgency", {}).get("name")
            if data.get("urgency")
            else None,
            status=data.get("status", {}).get("name")
            if data.get("status")
            else None,
            group_name=data.get("group", {}).get("name")
            if data.get("group")
            else None,
            assignee_name=data.get("technician", {}).get("name")
            if data.get("technician")
            else None,
            resolution=data.get("resolution", {}).get("content")
            if data.get("resolution")
            else None,
            created_time=self._parse_datetime(data.get("created_time")),
            closed_time=self._parse_datetime(data.get("closed_time")),
            raw_json=data,
        )

    def _parse_datetime(self, value: Any) -> datetime | None:
        """Parse ManageEngine datetime value."""
        if not value:
            return None
        if isinstance(value, dict):
            value = value.get("value")

        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value / 1000)

        if isinstance(value, str):
            try:
                if value.isdigit():
                    return datetime.fromtimestamp(int(value) / 1000)
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return None
        return None

    def _mock_response(self, endpoint: str) -> dict:
        """Return mock data for testing."""
        if "requests" in endpoint:
            return {
                "requests": [
                    {
                        "id": "1001",
                        "display_id": "REQ-001",
                        "subject": "VPN Connection Issues",
                        "description": "User unable to connect to office VPN.",
                        "short_description": "VPN Connection Issues",
                        "category": {"name": "Network"},
                        "priority": {"name": "High"},
                        "status": {"name": "Resolved"},
                        "group": {"name": "Network Support"},
                        "technician": {"name": "John Doe"},
                        "created_time": {
                            "value": int(
                                (datetime.utcnow() - timedelta(hours=2)).timestamp()
                                * 1000
                            )
                        },
                        "resolution": {"content": "Reset user profile on VPN."},
                    }
                ],
                "response_status": {"status": "success"},
            }
        return {"status": "success", "message": "Mock mode active"}
