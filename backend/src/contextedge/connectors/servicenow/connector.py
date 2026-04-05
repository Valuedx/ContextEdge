"""ServiceNow connector via REST Table API.

Supports incident, problem, change, and KB article retrieval
with timestamp-based checkpointing and journal/comment extraction.
"""

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

    async def _snow_get(self, path: str, params: dict | None = None) -> dict:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self.instance_url}{path}",
                auth=self._auth(),
                headers={"Accept": "application/json"},
                params=params,
            )
            resp.raise_for_status()
            return resp.json()

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
            events.append(
                IngestionEvent(
                    external_id=record.get("sys_id", ""),
                    source_type="servicenow",
                    object_type=table_name,
                    content=record,
                    thread_id=record.get("number"),
                    timestamp=_parse_snow_datetime(record.get("sys_updated_on")),
                    metadata={"table": table_name},
                )
            )

        has_more = len(records) == 100
        new_offset = offset + len(records)
        return BackfillResult(
            events=events,
            new_checkpoint=Checkpoint(data={"offset": new_offset}) if has_more else None,
            items_processed=len(events),
            has_more=has_more,
        )

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
        params = {
            "sysparm_fields": f"sys_id,{fields}",
            "sysparm_query": f"sys_updated_on>{last_updated}^ORDERBYsys_updated_on",
            "sysparm_limit": "200",
        }

        data = await self._snow_get(f"/api/now/table/{table_name}", params)
        records = data.get("result", [])

        latest_ts = last_updated
        for record in records:
            ts = record.get("sys_updated_on", "")
            if ts > latest_ts:
                latest_ts = ts
            events.append(
                IngestionEvent(
                    external_id=record.get("sys_id", ""),
                    source_type="servicenow",
                    object_type=table_name,
                    content=record,
                    thread_id=record.get("number"),
                    timestamp=_parse_snow_datetime(ts),
                    metadata={"table": table_name},
                )
            )

        return ChangeResult(
            events=events,
            new_checkpoint=Checkpoint(data={"last_updated": latest_ts}),
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
