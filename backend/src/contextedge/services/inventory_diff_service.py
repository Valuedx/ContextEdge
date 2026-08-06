"""Inventory diffing: silent changes become events (roadmap B3).

Nobody emits an event for a browser auto-upgrade — the change that
caused the F4 web-driver incident had no record anywhere. The only way
to catch that class is to OBSERVE agent-side state (versions, config
hashes) periodically and diff it against the last observation. Each
changed key becomes one state-transition event (B2), linked to the CI,
LLM-free.

The previous snapshot lives in ``Entity.attributes`` under a reserved
key — the CI entity is the natural owner and no new table is needed.
The FIRST observation of a CI stores a baseline and emits nothing: a
baseline is not a transition, and flooding the graph with "events" on
day one would poison the diagnosis window.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import structlog
from sqlalchemy import select

from contextedge.models.entity import Entity
from contextedge.services.event_evidence_service import record_state_event

logger = structlog.get_logger()

SNAPSHOT_KEY = "_inventory_snapshot"
SNAPSHOT_AT_KEY = "_inventory_snapshot_at"

# A corrupted or wildly reshaped report must not mint hundreds of
# events; past this the observation is a re-image, not a set of changes.
MAX_EVENTS_PER_OBSERVATION = 20


def diff_states(previous: dict, current: dict) -> list[tuple[str, str | None, str | None]]:
    """(key, from, to) per changed key. Values compared as strings —
    inventory values are version strings and hashes, not structures."""
    changes: list[tuple[str, str | None, str | None]] = []
    for key in sorted(set(previous) | set(current)):
        before = previous.get(key)
        after = current.get(key)
        before_s = str(before) if before is not None else None
        after_s = str(after) if after is not None else None
        if before_s != after_s:
            changes.append((key, before_s, after_s))
    return changes


async def observe_inventory(
    db,
    tenant_id: uuid.UUID,
    *,
    ci_name: str,
    state: dict,
    observed_at: datetime | None = None,
    source_label: str = "inventory_diff",
) -> dict:
    """Diff one CI's reported state against its stored snapshot; emit
    one B2 event per changed key; store the new snapshot."""
    observed_at = observed_at or datetime.now(UTC)
    counts = {"events": 0, "baseline": False, "changes": 0}
    state = {str(k)[:120]: str(v)[:300] for k, v in (state or {}).items()}

    entity = (
        await db.execute(
            select(Entity)
            .where(Entity.tenant_id == tenant_id, Entity.name == ci_name[:255])
            .limit(1)
        )
    ).scalar_one_or_none()
    if entity is None:
        entity = Entity(
            tenant_id=tenant_id,
            name=ci_name[:255],
            entity_type="configuration_item",
        )
        db.add(entity)
        await db.flush()

    attributes = dict(entity.attributes or {})
    previous = attributes.get(SNAPSHOT_KEY)

    if previous is not None:
        changes = diff_states(previous, state)
        counts["changes"] = len(changes)
        if len(changes) > MAX_EVENTS_PER_OBSERVATION:
            logger.warning(
                "inventory_diff.report_reshaped",
                ci=ci_name[:80],
                changes=len(changes),
            )
            changes = changes[:MAX_EVENTS_PER_OBSERVATION]
        for key, before, after in changes:
            ev = await record_state_event(
                db,
                tenant_id,
                ci_name=ci_name,
                event_kind=key,
                from_value=before,
                to_value=after,
                occurred_at=observed_at,
                source_label=source_label,
                domain_id=entity.domain_id,
            )
            if ev is not None:
                counts["events"] += 1
    else:
        counts["baseline"] = True

    attributes[SNAPSHOT_KEY] = state
    attributes[SNAPSHOT_AT_KEY] = observed_at.isoformat()
    entity.attributes = attributes
    await db.flush()
    return counts
