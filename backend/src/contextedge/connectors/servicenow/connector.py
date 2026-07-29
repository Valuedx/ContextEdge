"""ServiceNow connector via REST Table API.

Supports incident, problem, change, and KB article retrieval with
compound ``(sys_updated_on, sys_id)`` checkpointing (no boundary-second
misses), paged incremental sync, retry/backoff with Retry-After
handling, and journal/comment extraction.
"""

import asyncio
from datetime import datetime, timezone
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

TABLES = {
    "incident": {"label": "Incidents", "fields": "number,short_description,description,state,priority,assigned_to,opened_at,resolved_at,close_notes,sys_updated_on"},
    "problem": {"label": "Problems", "fields": "number,short_description,description,state,priority,assigned_to,opened_at,resolved_at,sys_updated_on"},
    "change_request": {"label": "Change Requests", "fields": "number,short_description,description,state,type,assigned_to,start_date,end_date,sys_updated_on"},
    "kb_knowledge": {"label": "KB Articles", "fields": "number,short_description,text,workflow_state,author,sys_updated_on"},
}


class ServiceNowConnector(BaseConnector):
    """Connector for ServiceNow REST Table API."""

    def __init__(self, source_config: dict[str, Any], credentials: dict[str, Any]):
        super().__init__(source_config, credentials)
        self.instance_url = credentials.get("instance_url", "").rstrip("/")

    def _auth(self) -> tuple[str, str]:
        return (self.credentials["username"], self.credentials["password"])

    # Retry transient failures (429 / 5xx / transport errors) with capped
    # exponential backoff, honoring Retry-After on 429.
    MAX_ATTEMPTS = 3
    BACKOFF_BASE_SECONDS = 1.0
    MAX_RETRY_AFTER_SECONDS = 60.0

    async def _snow_get(self, path: str, params: dict | None = None) -> dict:
        last_exc: Exception | None = None
        for attempt in range(1, self.MAX_ATTEMPTS + 1):
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    resp = await client.get(
                        f"{self.instance_url}{path}",
                        auth=self._auth(),
                        headers={"Accept": "application/json"},
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
                    raise  # 4xx (auth, bad request) will not improve on retry
                if attempt < self.MAX_ATTEMPTS:
                    await asyncio.sleep(self.BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)))
        raise last_exc  # exhausted retries

    async def validate_credentials(self) -> CredentialStatus:
        try:
            data = await self._snow_get(
                "/api/now/table/incident",
                {"sysparm_limit": "1", "sysparm_fields": "number"},
            )
            return CredentialStatus(valid=True, message="ServiceNow API access verified")
        except Exception as e:
            return CredentialStatus(valid=False, message=str(e))

    async def discover_objects(self) -> list[DiscoveredObject]:
        objects: list[DiscoveredObject] = []
        for table_name, meta in TABLES.items():
            count_data = await self._snow_get(
                f"/api/now/stats/{table_name}",
                {"sysparm_count": "true"},
            )
            count = count_data.get("result", {}).get("stats", {}).get("count", "0")
            objects.append(
                DiscoveredObject(
                    external_id=table_name,
                    object_type="servicenow_table",
                    display_name=f"{meta['label']} ({table_name})",
                    metadata={"record_count": int(count), "table": table_name},
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
        table_name = object_id
        table_meta = TABLES.get(table_name, {})
        fields = table_meta.get("fields", "sys_id,short_description,sys_updated_on")
        events: list[IngestionEvent] = []

        offset = checkpoint.data.get("offset", 0) if checkpoint else 0
        start_str = window.start.strftime("%Y-%m-%d %H:%M:%S")
        end_str = window.end.strftime("%Y-%m-%d %H:%M:%S")

        params = {
            "sysparm_fields": f"sys_id,{fields}",
            "sysparm_query": f"sys_updated_onBETWEEN{start_str}@{end_str}^ORDERBYsys_updated_on",
            "sysparm_limit": "100",
            "sysparm_offset": str(offset),
        }

        data = await self._snow_get(f"/api/now/table/{table_name}", params)
        records = data.get("result", [])

        for record in records:
            sys_id = record.get("sys_id", "")
            events.append(
                IngestionEvent(
                    external_id=sys_id,
                    source_type="servicenow",
                    object_type=table_name,
                    content=record,
                    thread_id=f"{table_name}:{sys_id}",
                    timestamp=_parse_snow_datetime(record.get("sys_updated_on")),
                    metadata={"table": table_name},
                )
            )

        has_more = len(records) == 100
        new_offset = offset + len(records)

        if has_more:
            new_checkpoint = Checkpoint(data={"offset": new_offset})
        else:
            latest_ts = ""
            latest_sys_id = ""
            for record in records:
                ts = record.get("sys_updated_on", "")
                sys_id = record.get("sys_id", "")
                if (ts, sys_id) > (latest_ts, latest_sys_id):
                    latest_ts, latest_sys_id = ts, sys_id
            if not latest_ts:
                # Empty final page: clamp the seed to "now" when the window
                # extends into the future, or records updated between this
                # backfill and window.end would be skipped by incremental.
                now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                latest_ts = min(end_str, now_str)
            # Seed the compound checkpoint fetch_changes resumes from.
            new_checkpoint = Checkpoint(
                data={"last_updated": latest_ts, "last_sys_id": latest_sys_id}
            )

        return BackfillResult(
            events=events,
            new_checkpoint=new_checkpoint,
            items_processed=len(events),
            has_more=has_more,
        )

    # Incremental sync: page size × max pages bounds one invocation; the
    # compound checkpoint means the next tick resumes exactly where this
    # one stopped.
    INCREMENTAL_PAGE_SIZE = 200
    INCREMENTAL_MAX_PAGES = 10

    async def fetch_changes(
        self,
        object_id: str,
        object_type: str,
        checkpoint: Checkpoint,
    ) -> ChangeResult:
        table_name = object_id
        table_meta = TABLES.get(table_name, {})
        fields = table_meta.get("fields", "sys_id,short_description,sys_updated_on")
        events: list[IngestionEvent] = []

        last_updated = checkpoint.data.get("last_updated", "2000-01-01 00:00:00")
        last_sys_id = str(checkpoint.data.get("last_sys_id", ""))

        # Keyset pagination on the compound (sys_updated_on, sys_id) cursor.
        # NEVER sysparm_offset: offset pages over a table that is being
        # updated between fetches shift rows left and silently skip records
        # — and because the checkpoint then advances past them, they were
        # lost forever. Each page instead re-queries strictly after the last
        # tuple seen (the ^NQ branch handles rows sharing the boundary
        # second), so concurrent updates can only re-deliver, never skip.
        cursor_ts = last_updated
        cursor_sys_id = last_sys_id
        latest_ts = last_updated
        latest_sys_id = last_sys_id
        for _page in range(self.INCREMENTAL_MAX_PAGES):
            params = {
                "sysparm_fields": f"sys_id,{fields}",
                "sysparm_query": (
                    f"sys_updated_on>{cursor_ts}"
                    f"^NQsys_updated_on={cursor_ts}^sys_id>{cursor_sys_id}"
                    "^ORDERBYsys_updated_on^ORDERBYsys_id"
                ),
                "sysparm_limit": str(self.INCREMENTAL_PAGE_SIZE),
            }
            data = await self._snow_get(f"/api/now/table/{table_name}", params)
            records = data.get("result", [])

            # Fail-closed ordering guard: the cursor advance is only correct
            # if the server honored ORDERBY across the ^NQ branches. If a
            # page arrives out of order, stop WITHOUT advancing past the
            # previous page — refetching next tick is safe (dedup
            # downstream); skipping unreturned rows is silent data loss.
            page_tuples = [
                (r.get("sys_updated_on", ""), r.get("sys_id", "")) for r in records
            ]
            if page_tuples != sorted(page_tuples):
                import structlog

                structlog.get_logger().error(
                    "servicenow.page_order_violation",
                    table=table_name,
                    page_size=len(records),
                )
                break

            for record in records:
                ts = record.get("sys_updated_on", "")
                sys_id = record.get("sys_id", "")
                if (ts, sys_id) <= (cursor_ts, cursor_sys_id):
                    continue  # server returned an already-seen row; skip
                if (ts, sys_id) > (latest_ts, latest_sys_id):
                    latest_ts, latest_sys_id = ts, sys_id
                events.append(
                    IngestionEvent(
                        external_id=sys_id,
                        source_type="servicenow",
                        object_type=table_name,
                        content=record,
                        thread_id=f"{table_name}:{sys_id}",
                        timestamp=_parse_snow_datetime(ts),
                        metadata={"table": table_name},
                    )
                )

            if records:
                cursor_ts = records[-1].get("sys_updated_on", cursor_ts)
                cursor_sys_id = records[-1].get("sys_id", cursor_sys_id)
            if len(records) < self.INCREMENTAL_PAGE_SIZE:
                break

        return ChangeResult(
            events=events,
            new_checkpoint=Checkpoint(
                data={"last_updated": latest_ts, "last_sys_id": latest_sys_id}
            ),
            items_processed=len(events),
        )

    async def hydrate_thread(self, thread_ref: str) -> HydratedThread:
        """Fetch record with journal entries (comments/work notes)."""
        parts = thread_ref.split(":")
        table_name, sys_id = parts[0], parts[1]

        record = await self._snow_get(f"/api/now/table/{table_name}/{sys_id}")
        record_data = record.get("result", {})

        journal_data = await self._snow_get(
            f"/api/now/table/sys_journal_field",
            {
                "sysparm_query": f"element_id={sys_id}^ORDERBYsys_created_on",
                "sysparm_fields": "value,element,sys_created_on,sys_created_by",
                "sysparm_limit": "500",
            },
        )

        messages = []
        for entry in journal_data.get("result", []):
            messages.append({
                "id": f"{sys_id}_{entry.get('sys_created_on', '')}",
                "body": entry.get("value", ""),
                "type": entry.get("element", ""),
                "from": entry.get("sys_created_by", ""),
                "timestamp": entry.get("sys_created_on"),
            })

        participants = {m.get("from") for m in messages if m.get("from")}
        return HydratedThread(
            thread_id=thread_ref,
            messages=messages,
            participant_count=len(participants),
            metadata={"record": record_data},
        )

    def rate_limit_config(self) -> RateLimitConfig:
        return RateLimitConfig(requests_per_second=10.0, burst_size=20)


def _parse_snow_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
