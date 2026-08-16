"""Which tickets get processed first.

A backfill of a thousand resolved tickets is not a thousand equal items. On
this tenant, 68% carry a resolution note and 84% a human-assigned root cause,
and those are the ones the whole product exists to learn from. The rest still
get ingested — nothing is dropped — but enrichment is a queue, and what sits
at its head decides what a graph is worth after the first hour rather than
after the last.

Ordering happens at the handoff to normalization, not at fetch: a list row
does not carry `resolution` (it arrives on the detail call), so the only
place that can rank on it is after the raw object exists.

Modes, set per source object as ``ingest_priority``:

- ``resolution_first`` — tickets carrying a resolution note, then the
  longest conversations. What a learning corpus wants.
- ``threads_desc`` — longest conversations first. What a debugging corpus
  wants: the richest evidence, and the worst cost, so failures show up early.
- ``threads_asc`` — shortest first. Maximum tickets per unit spend, useful
  when the budget is the constraint.
- ``none`` — arrival order (default). Deterministic and boring, which is the
  right default for a mode nobody has thought about.
"""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy import Integer, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger()

INGEST_PRIORITIES = ("none", "resolution_first", "threads_desc", "threads_asc")
DEFAULT_INGEST_PRIORITY = "none"

# Payload keys carrying the conversation size, in the order they are tried.
_THREAD_COUNT_KEYS = ("thread_count", "comment_count")
_RESOLUTION_KEYS = ("resolution",)


def _ingest_priority(source_object) -> str:
    """The mode this source object asks for, or the default."""
    meta = getattr(source_object, "metadata_extra", None) or {}
    value = str(meta.get("ingest_priority") or DEFAULT_INGEST_PRIORITY).strip()
    if value not in INGEST_PRIORITIES:
        logger.warning(
            "ingest_priority.unknown", value=value[:40], using=DEFAULT_INGEST_PRIORITY
        )
        return DEFAULT_INGEST_PRIORITY
    return value


async def order_raw_ids_by_priority(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    raw_ids: list[uuid.UUID],
    priority: str = DEFAULT_INGEST_PRIORITY,
) -> list[uuid.UUID]:
    """Re-order the normalization handoff. Never adds or drops an id.

    Fail-soft: an ordering that errors returns the original list. Losing the
    order costs some sequencing; losing the ids would cost the ingest.
    """
    if priority == DEFAULT_INGEST_PRIORITY or len(raw_ids) < 2:
        return raw_ids

    from contextedge.models.evidence import RawEvidenceObject

    payload = RawEvidenceObject.raw_payload
    # COALESCE over the known keys, cast to int; a payload without any of
    # them sorts as 0 rather than dropping out of the ordering.
    threads = func.coalesce(
        *[func.nullif(payload[key].astext, "") for key in _THREAD_COUNT_KEYS],
        "0",
    ).cast(Integer)
    has_resolution = case(
        (
            func.coalesce(
                func.nullif(payload[_RESOLUTION_KEYS[0]].astext, ""), None
            ).is_not(None),
            1,
        ),
        else_=0,
    )

    if priority == "resolution_first":
        order = (has_resolution.desc(), threads.desc())
    elif priority == "threads_desc":
        order = (threads.desc(),)
    else:  # threads_asc
        order = (threads.asc(),)

    try:
        rows = (
            await db.execute(
                select(RawEvidenceObject.id)
                .where(
                    RawEvidenceObject.tenant_id == tenant_id,
                    RawEvidenceObject.id.in_(tuple(raw_ids)),
                )
                .order_by(*order)
            )
        ).scalars().all()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "ingest_priority.order_failed",
            priority=priority,
            error_type=type(exc).__name__,
        )
        return raw_ids

    ordered = [rid for rid in rows if rid in set(raw_ids)]
    # Anything the query could not see (a row not yet visible to this
    # transaction) keeps its place at the end rather than being lost.
    missing = [rid for rid in raw_ids if rid not in set(ordered)]
    result = ordered + missing
    logger.info(
        "ingest_priority.applied",
        priority=priority,
        count=len(result),
        unordered=len(missing),
    )
    return result
