"""ServiceNow connector via REST Table API.

Supports incident, problem, change, and KB article retrieval with
compound ``(sys_updated_on, sys_id)`` checkpointing (no boundary-second
misses), paged incremental sync, retry/backoff with Retry-After
handling, and journal/comment extraction.
"""

import asyncio
from datetime import UTC, datetime
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

# Reference fields (problem_id, rfc, caused_by, parent_incident, cmdb_ci,
# assignment_group) serialize as {"value": <sys_id>, "link": ...} under the
# default sysparm_display_value=false; the dot-walked companions
# (cmdb_ci.name, ...) come back as flat display strings. Both feed
# services/servicenow_reference_service.py — deterministic case links,
# typed graph edges, and CI / team entities. Keep sysparm_display_value
# at its default: flipping it to all/true turns EVERY field into a dict
# and breaks the (sys_updated_on, sys_id) keyset checkpoint parsing.
TABLES = {
    "incident": {
        "label": "Incidents",
        "fields": (
            "number,short_description,description,state,priority,assigned_to,"
            "opened_at,resolved_at,close_notes,close_code,category,sys_updated_on,"
            "problem_id,rfc,caused_by,parent_incident,cmdb_ci,cmdb_ci.name,"
            "cmdb_ci.sys_class_name,cmdb_ci.manufacturer.name,"
            "cmdb_ci.model_id.name,cmdb_ci.os,cmdb_ci.os_version,"
            "assignment_group,assignment_group.name"
        ),
    },
    "problem": {
        "label": "Problems",
        "fields": (
            "number,short_description,description,state,priority,assigned_to,"
            "opened_at,resolved_at,sys_updated_on,rfc,cmdb_ci,cmdb_ci.name,"
            "cmdb_ci.sys_class_name,cmdb_ci.manufacturer.name,"
            "cmdb_ci.model_id.name,cmdb_ci.os,cmdb_ci.os_version,"
            "assignment_group,assignment_group.name"
        ),
    },
    "change_request": {
        "label": "Change Requests",
        "fields": (
            "number,short_description,description,state,type,assigned_to,"
            "start_date,end_date,work_start,work_end,close_code,category,"
            "sys_updated_on,cmdb_ci,"
            "cmdb_ci.name,cmdb_ci.sys_class_name,cmdb_ci.manufacturer.name,"
            "cmdb_ci.model_id.name,cmdb_ci.os,cmdb_ci.os_version,"
            "assignment_group,assignment_group.name"
        ),
    },
    "kb_knowledge": {
        "label": "KB Articles",
        "fields": "number,short_description,text,workflow_state,author,sys_updated_on",
    },
    # The request side of ITSM. Incidents record what broke; requests record
    # what was provisioned and how — which is the answer to a large share of
    # service-desk questions ("how do we set up a new starter's laptop").
    #
    # The chain is REQ -> RITM -> SCTASK. Only the last two are ingested: the
    # REQ header is an envelope with no subject of its own — verified against a
    # live instance, it normalises to "Untitled Evidence" — so ingesting it
    # would pay to classify empty records. Each RITM carries `request.number`,
    # so the request it belonged to is still recoverable without storing the
    # envelope itself.
    "sc_req_item": {
        "label": "Requested Items",
        # A RITM carries no short_description of its own — the catalog item is
        # the subject ("Standard Laptop"), so cat_item.name is dot-walked in
        # and promoted to the title below. Without that, every requested item
        # lands as untitled evidence.
        "fields": (
            "number,short_description,description,state,stage,approval,priority,"
            "opened_at,closed_at,close_notes,request,request.number,"
            "cat_item,cat_item.name,requested_for,requested_for.name,"
            "assignment_group,assignment_group.name,cmdb_ci,cmdb_ci.name,"
            "sys_updated_on"
        ),
    },
    "sc_task": {
        "label": "Catalog Tasks",
        "fields": (
            "number,short_description,description,state,priority,opened_at,"
            "closed_at,close_notes,request_item,request_item.number,"
            "assigned_to,assignment_group,assignment_group.name,sys_updated_on"
        ),
    },
    # em_alert never produces per-record events — each sync invocation
    # rolls fetched alerts up per (CI, day) in alert_rollup.py. Severity
    # is filtered server-side (see _table_extra_query); the checkpoint
    # cursor still advances on the RAW alert rows, so rollup batching
    # cannot skip records.
    "em_alert": {
        "label": "EM Alerts (rolled up)",
        "fields": (
            "number,severity,state,short_description,description,source,"
            "cmdb_ci,cmdb_ci.name,incident,initial_event_time,last_event_time,"
            "sys_updated_on"
        ),
    },
}

# When the thing described actually HAPPENED, per table, most specific first.
#
# Evidence timestamps used to be `sys_updated_on` for every record, which is
# when someone last touched it — an incident opened in January and re-assigned
# yesterday looked like it happened yesterday. That is harmless for the
# checkpoint (which must keep using sys_updated_on: it is the only monotonic
# cursor) and wrong for everything that reasons about time. Situation onset,
# the change→incident interval, and any "what else was happening around then"
# question all read this field, and all of them silently collapse when every
# record from one backfill carries the same timestamp.
#
# kb_knowledge is deliberately absent: an article has no occurrence time, and
# its last update is the most meaningful date it has.
EVENT_TIME_FIELDS: dict[str, tuple[str, ...]] = {
    "incident": ("opened_at",),
    "problem": ("opened_at",),
    # A change's event is its execution, not its paperwork. work_start is when
    # someone actually began; start_date is when they were approved to. The
    # gap between them is itself a signal (a change run outside its window),
    # so both are fetched and the actual one wins.
    "change_request": ("work_start", "start_date"),
    "sc_req_item": ("opened_at",),
    "sc_task": ("opened_at",),
    "em_alert": ("initial_event_time",),
}

# Alerts at or below this severity number are ingested (1=critical …
# 5=info). Overridable per source via source_config["alert_severity_max"].
DEFAULT_ALERT_SEVERITY_MAX = 3

logger = structlog.get_logger()


class ServiceNowConnector(BaseConnector):
    """Connector for ServiceNow REST Table API."""

    def __init__(self, source_config: dict[str, Any], credentials: dict[str, Any]):
        super().__init__(source_config, credentials)
        self.instance_url = credentials.get("instance_url", "").rstrip("/")

    def _auth(self) -> tuple[str, str]:
        return (self.credentials["username"], self.credentials["password"])

    def _table_extra_query(self, table_name: str) -> str:
        """Per-table server-side filter, appended to EVERY branch of a
        sysparm_query (a ^NQ branch is a fresh query — a filter appended
        to only one branch silently leaks through the other).

        Two sources of filter, combined:

        - ``alert_severity_max`` bounds em_alert severity (default 3).
        - ``table_filters`` scopes any table to a subset, as raw encoded-query
          syntax keyed by table name, e.g.::

              {"table_filters": {"incident": "priority<=2"}}

          Syncing an entire ServiceNow instance is rarely what anyone wants —
          most tenants care about a queue, a priority band, or an assignment
          group. Filtering server-side means the records never leave
          ServiceNow, so they cost no extraction and no storage. Applied to
          both the backfill and the keyset-paged incremental query.
        """
        parts: list[str] = []

        if table_name == "em_alert":
            try:
                max_severity = int(
                    (self.source_config or {}).get(
                        "alert_severity_max", DEFAULT_ALERT_SEVERITY_MAX
                    )
                )
            except (TypeError, ValueError):
                max_severity = DEFAULT_ALERT_SEVERITY_MAX
            if not 1 <= max_severity <= 5:
                max_severity = DEFAULT_ALERT_SEVERITY_MAX
            parts.append(f"^severity<={max_severity}")

        custom = ((self.source_config or {}).get("table_filters") or {}).get(table_name)
        if isinstance(custom, str) and custom.strip():
            # Tolerate a leading ^ so both "priority=1" and "^priority=1" work.
            parts.append("^" + custom.strip().lstrip("^"))

        return "".join(parts)

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
            await self._snow_get(
                "/api/now/table/incident",
                {"sysparm_limit": "1", "sysparm_fields": "number"},
            )
            return CredentialStatus(valid=True, message="ServiceNow API access verified")
        except Exception as e:
            return CredentialStatus(valid=False, message=str(e))

    async def discover_objects(self) -> list[DiscoveredObject]:
        """List the tables this instance actually exposes.

        A table is skipped rather than fatal when the instance does not have
        it. ``em_alert`` ships with ITOM Event Management, which is not
        activated on a stock instance, and the Table API answers 400 for a
        table that does not exist. Letting that abort discovery means an
        instance missing one optional plugin can offer *no* objects at all —
        incidents included — which is how a working ServiceNow source looks
        completely broken. Verified against a PDI without ITOM: 4 tables
        discovered, em_alert skipped.
        """
        objects: list[DiscoveredObject] = []
        for table_name, meta in TABLES.items():
            try:
                count_data = await self._snow_get(
                    f"/api/now/stats/{table_name}",
                    {"sysparm_count": "true"},
                )
            except httpx.HTTPStatusError as exc:
                # 400 = no such table, 403 = present but not readable by this
                # account. Both mean "cannot sync it", and neither should cost
                # the caller the tables that do work.
                if exc.response.status_code in (400, 403, 404):
                    logger.info(
                        "servicenow.table_unavailable",
                        table=table_name,
                        status=exc.response.status_code,
                    )
                    continue
                raise
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
            "sysparm_query": (
                f"sys_updated_onBETWEEN{start_str}@{end_str}"
                f"{self._table_extra_query(table_name)}"
                "^ORDERBYsys_updated_on"
            ),
            "sysparm_limit": "100",
            "sysparm_offset": str(offset),
        }

        data = await self._snow_get(f"/api/now/table/{table_name}", params)
        records = data.get("result", [])

        if table_name == "em_alert":
            from contextedge.connectors.servicenow.alert_rollup import (
                rollup_alert_events,
            )

            events.extend(rollup_alert_events(records))
        else:
            for record in records:
                sys_id = record.get("sys_id", "")
                events.append(
                    IngestionEvent(
                        external_id=sys_id,
                        source_type="servicenow",
                        object_type=table_name,
                        content=_with_derived_title(record),
                        thread_id=f"{table_name}:{sys_id}",
                        timestamp=_event_time(table_name, record),
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
                now_str = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
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
        extra = self._table_extra_query(table_name)
        alert_records: list[dict] = []
        for _page in range(self.INCREMENTAL_MAX_PAGES):
            params = {
                "sysparm_fields": f"sys_id,{fields}",
                "sysparm_query": (
                    f"sys_updated_on>{cursor_ts}{extra}"
                    f"^NQsys_updated_on={cursor_ts}^sys_id>{cursor_sys_id}{extra}"
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
                if table_name == "em_alert":
                    # Alerts roll up after pagination — one event per
                    # (CI, day) group, never per record.
                    alert_records.append(record)
                    continue
                events.append(
                    IngestionEvent(
                        external_id=sys_id,
                        source_type="servicenow",
                        object_type=table_name,
                        content=_with_derived_title(record),
                        thread_id=f"{table_name}:{sys_id}",
                        timestamp=_event_time(table_name, record),
                        metadata={"table": table_name},
                    )
                )

            if records:
                cursor_ts = records[-1].get("sys_updated_on", cursor_ts)
                cursor_sys_id = records[-1].get("sys_id", cursor_sys_id)
            if len(records) < self.INCREMENTAL_PAGE_SIZE:
                break

        if alert_records:
            from contextedge.connectors.servicenow.alert_rollup import (
                rollup_alert_events,
            )

            events.extend(rollup_alert_events(alert_records))

        return ChangeResult(
            events=events,
            new_checkpoint=Checkpoint(
                data={"last_updated": latest_ts, "last_sys_id": latest_sys_id}
            ),
            items_processed=len(events),
        )

    async def hydrate_thread(self, thread_ref: str) -> HydratedThread:
        """Fetch record with journal entries (comments/work notes)."""
        if thread_ref.startswith("em_alert_rollup:"):
            # Rollup threads aggregate many alerts; there is no single
            # record or journal to fetch — the rollup evidence bodies ARE
            # the thread content.
            return HydratedThread(
                thread_id=thread_ref,
                messages=[],
                participant_count=0,
                metadata={"rollup": True},
            )
        parts = thread_ref.split(":")
        table_name, sys_id = parts[0], parts[1]

        record = await self._snow_get(f"/api/now/table/{table_name}/{sys_id}")
        record_data = record.get("result", {})

        journal_data = await self._snow_get(
            "/api/now/table/sys_journal_field",
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

    # CMDB topology (services/cmdb_topology_service.py): live ±1-hop
    # lookups, NOT bulk sync — the CMDB stays in ServiceNow; ContextEdge
    # caches only the neighborhoods that operational reality touches.

    CMDB_REL_PAGE_SIZE = 200

    async def fetch_ci_relationships(self, sys_id: str) -> list[dict]:
        """cmdb_rel_ci rows where the CI is parent or child, bounded at
        CMDB_REL_PAGE_SIZE — a hub CI's neighborhood is truncated, not
        paged (±1-hop context, not a replica). type.name is dot-walked so
        the relationship label arrives without a second call."""
        data = await self._snow_get(
            "/api/now/table/cmdb_rel_ci",
            {
                "sysparm_query": f"parent={sys_id}^ORchild={sys_id}",
                "sysparm_fields": "sys_id,parent,child,type.name",
                "sysparm_limit": str(self.CMDB_REL_PAGE_SIZE),
            },
        )
        return data.get("result", [])

    async def fetch_ci_details(self, sys_ids: list[str]) -> list[dict]:
        """Name / class / status for a bounded set of CIs in one call."""
        if not sys_ids:
            return []
        data = await self._snow_get(
            "/api/now/table/cmdb_ci",
            {
                "sysparm_query": "sys_idIN" + ",".join(sys_ids[:200]),
                "sysparm_fields": (
                    "sys_id,name,sys_class_name,operational_status,"
                    # B2 traits. os/os_version exist only on computer
                    # subclasses — ServiceNow returns them empty for
                    # other classes, which lands as absent traits.
                    # busines_criticality [sic — ServiceNow's own field
                    # spelling] + support_group feed the C2 criticality/
                    # owner facts; absent on a class -> absent, never
                    # guessed.
                    #
                    # owned_by is a PERSON and support_group is a TEAM, and
                    # they answer different questions at 3am: who is
                    # accountable versus who is on call. Escalating to a
                    # named individual who left the company is worse than
                    # escalating to a queue, so both are carried and neither
                    # substitutes for the other.
                    #
                    # busines_criticality exists only on cmdb_ci_service, not
                    # on the cmdb_ci base table this queries. ServiceNow
                    # returns it empty for every other class rather than
                    # erroring, which is why criticality reaches a switch
                    # through the service that depends on it rather than
                    # being stamped on the switch.
                    "manufacturer.name,model_id.name,os,os_version,"
                    "support_group.name,owned_by.name"
                ),
                "sysparm_limit": "200",
            },
        )
        rows = data.get("result", [])

        # Criticality needs a second call against the SERVICE table.
        #
        # `busines_criticality` is defined on cmdb_ci_service, not on the
        # cmdb_ci base table queried above. Asking the base table for it does
        # not error — the column is simply absent from every row — so the
        # request looked correct and returned nothing, permanently. Verified
        # live: the same sys_id returns `busines_criticality` from
        # /table/cmdb_ci_service and omits the key entirely from /table/cmdb_ci.
        #
        # Only fires when the neighborhood actually contains a service, so a
        # pure-infrastructure walk still costs two calls.
        service_ids = [
            r.get("sys_id")
            for r in rows
            if str(r.get("sys_class_name") or "").startswith("cmdb_ci_service")
            and r.get("sys_id")
        ]
        if service_ids:
            service_data = await self._snow_get(
                "/api/now/table/cmdb_ci_service",
                {
                    "sysparm_query": "sys_idIN" + ",".join(service_ids[:200]),
                    "sysparm_fields": "sys_id,busines_criticality",
                    "sysparm_limit": "200",
                },
            )
            criticality_by_id = {
                r.get("sys_id"): r.get("busines_criticality")
                for r in service_data.get("result", [])
                if r.get("busines_criticality")
            }
            for row in rows:
                value = criticality_by_id.get(row.get("sys_id"))
                if value:
                    row["busines_criticality"] = value
        return rows

    def rate_limit_config(self) -> RateLimitConfig:
        return RateLimitConfig(requests_per_second=10.0, burst_size=20)


def _with_derived_title(record: dict) -> dict:
    """Give a record a usable ``short_description`` when the table has none.

    ``evidence_title_from_payload`` looks for title/subject/short_description
    and then falls back to a body snippet. A requested item has an empty
    ``short_description`` — its subject is the catalog item ("Standard
    Laptop"), which arrives dot-walked as ``cat_item.name`` and so is invisible
    to that helper. Untitled evidence is hard to review and hard to cite, so
    the mapping is done here, where the ServiceNow-specific knowledge lives,
    rather than teaching the generic normaliser about catalog items.

    Returns the record unchanged when it already has a description.
    """
    if str(record.get("short_description") or "").strip():
        return record
    subject = str(record.get("cat_item.name") or "").strip()
    if not subject:
        return record
    enriched = dict(record)
    enriched["short_description"] = subject
    return enriched


def _event_time(table_name: str, record: dict) -> datetime | None:
    """When this record's subject occurred, falling back to last update.

    Falls back rather than returning None: a record with no occurrence field
    still needs to be orderable, and `sys_updated_on` is a worse answer than
    `opened_at` but a much better one than nothing.
    """
    for field_name in EVENT_TIME_FIELDS.get(table_name, ()):
        parsed = _parse_snow_datetime(record.get(field_name))
        if parsed is not None:
            return parsed
    return _parse_snow_datetime(record.get("sys_updated_on"))


def _parse_snow_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
    except ValueError:
        return None
