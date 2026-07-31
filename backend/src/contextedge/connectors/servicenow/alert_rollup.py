"""Per-(CI, day) rollups of ServiceNow Event Management alerts (Phase 3).

``em_alert`` is the deduplicated layer ServiceNow builds above raw
``em_event`` — but even alerts arrive orders of magnitude faster than
tickets. One evidence row (and one embedding) per alert would swamp
embedding spend, retention, and ANN quality with near-duplicate noise.

So alerts never become individual evidence. Each sync invocation groups
the fetched alerts by (affected CI, UTC day) and emits ONE ingestion
event per group, carrying counts, the severity distribution, bounded
sample lines (symptom vocabulary for the embedding), the CI reference in
the exact shape the Phase 1 extractor already understands, and the
sys_ids of incidents the alerts were promoted to. Every group re-fetched
later (alert state changes re-deliver rows) lands in the same thread via
a stable thread_id; identical batches dedupe on content hash.

What the graph gains: "the RADIUS timeouts started 40 minutes before the
first user called" — the telemetry timeline preceding an incident,
reachable from the incident (typed ``preceded_incident`` edges) and from
the CI entity (``affects_ci``).
"""

from __future__ import annotations

from contextedge.connectors.base import IngestionEvent

SEVERITY_LABELS = {1: "critical", 2: "major", 3: "minor", 4: "warning", 5: "info"}
SAMPLE_LINES_CAP = 30
INCIDENT_REFS_CAP = 20


def field_value(raw: object) -> str:
    """Scalar from a Table API field in any serialization: reference dict
    ({"value", "link"}), display_value=all dict, or plain string."""
    if isinstance(raw, dict):
        raw = raw.get("value")
    if isinstance(raw, str):
        return raw.strip()
    return ""


def _severity(record: dict) -> int | None:
    try:
        return int(field_value(record.get("severity")))
    except (TypeError, ValueError):
        return None


def rollup_alert_events(records: list[dict]) -> list[IngestionEvent]:
    """Group alert records into per-(CI, day) ingestion events."""
    groups: dict[tuple[str, str], list[dict]] = {}
    for record in records:
        ci = field_value(record.get("cmdb_ci"))
        day = (record.get("sys_updated_on") or "")[:10] or "unknown"
        groups.setdefault((ci, day), []).append(record)

    events: list[IngestionEvent] = []
    for (ci, day), group in sorted(groups.items()):
        ci_name = next(
            (n for n in (field_value(r.get("cmdb_ci.name")) for r in group) if n),
            "",
        )
        severities = [s for s in (_severity(r) for r in group) if s is not None]
        worst = min(severities) if severities else None
        worst_label = SEVERITY_LABELS.get(worst, "unknown")
        severity_counts: dict[str, int] = {}
        for s in severities:
            severity_counts[str(s)] = severity_counts.get(str(s), 0) + 1

        sample_lines = []
        for record in group[:SAMPLE_LINES_CAP]:
            label = SEVERITY_LABELS.get(_severity(record), "unknown")
            text = field_value(record.get("short_description")) or field_value(
                record.get("description")
            )
            number = field_value(record.get("number"))
            sample_lines.append(f"- [{label}] {text[:300]} ({number})".rstrip())

        incident_refs: list[str] = []
        for record in group:
            incident = field_value(record.get("incident"))
            if incident and incident not in incident_refs:
                incident_refs.append(incident)

        # Two separate series: the window opens at the earliest initial
        # time and closes at the latest last time. Each falls back to the
        # other field so a record carrying only one still participates.
        initial_times = sorted(
            t
            for t in (
                field_value(r.get("initial_event_time"))
                or field_value(r.get("last_event_time"))
                for r in group
            )
            if t
        )
        last_times = sorted(
            t
            for t in (
                field_value(r.get("last_event_time"))
                or field_value(r.get("initial_event_time"))
                for r in group
            )
            if t
        )

        subject = ci_name or ci or "unassigned CIs"
        title = (
            f"Alert activity on {subject} ({day}): "
            f"{len(group)} alerts, worst {worst_label}"
        )
        content = {
            "record_type": "em_alert_rollup",
            "short_description": title,
            "description": "\n".join(sample_lines),
            "bucket": day,
            "alert_count": len(group),
            "worst_severity": worst,
            "severity_counts": severity_counts,
            "alert_numbers": [
                n
                for n in (field_value(r.get("number")) for r in group[:SAMPLE_LINES_CAP])
                if n
            ],
            "first_event_time": initial_times[0] if initial_times else None,
            "last_event_time": last_times[-1] if last_times else None,
            # Promoted-incident references: typed graph edges ONLY — never
            # case-link keys (five unrelated incidents on one busy CI in a
            # day must not merge into one canonical case).
            "alert_incidents": incident_refs[:INCIDENT_REFS_CAP],
        }
        if ci:
            # Exact shape the Phase 1 entity extractor consumes — the CI
            # entity, affects_ci edge, and topology warm candidate all
            # come for free.
            content["cmdb_ci"] = {"value": ci}
            if ci_name:
                content["cmdb_ci.name"] = ci_name

        group_key = f"em_alert_rollup:{ci or 'unassigned'}:{day}"
        latest_updated = max(r.get("sys_updated_on") or "" for r in group)
        events.append(
            IngestionEvent(
                external_id=group_key,
                source_type="servicenow",
                object_type="em_alert_rollup",
                content=content,
                thread_id=group_key,
                timestamp=_parse_ts(latest_updated),
                metadata={"table": "em_alert", "rollup": True},
            )
        )
    return events


def _parse_ts(value: str):
    from contextedge.connectors.servicenow.connector import _parse_snow_datetime

    return _parse_snow_datetime(value)
