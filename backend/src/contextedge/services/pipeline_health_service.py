"""What the pipeline is actually doing right now.

Cost is already visible (`admin_cost_service`). Progress is not, and the
gap cost a whole run: on the live Zoho backfill the pipeline reported
success on every task while building no graph at all, because
`correlate_evidence` — a 0.25s task — sat behind 8,000 thirty-second
`normalize_evidence` tasks in one FIFO that was growing by ~70 tasks a
minute. Every number an operator could see said healthy. Evidence rose,
tokens rose, no task failed. Episodes stayed at zero for hours and nothing
anywhere said why.

The numbers that would have shown it are all here:

- **queue depth per lane**, and whether a lane is growing or draining.
  A queue that grows while being consumed is the signature of work that
  feeds itself (hydration turns one ticket into ~41 more normalize tasks),
  and it is invisible in any per-task metric.
- **throughput and latency**, separately. Slow calls and starved calls
  look identical from the outside — a p50 of 4s next to a queue of 8,000
  says the model is fine and the ordering is not.
- **the graph chain**, counted end to end: evidence -> correlations ->
  episodes -> patterns -> playbooks. Each stage feeds the next, so the
  first zero in that sequence is the diagnosis. It is the one view that
  distinguishes "still working" from "will never produce anything".

Read-only and cheap: counts and percentiles over indexed columns, plus a
`LLEN` per queue. Safe to poll from a dashboard.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger()

# The lanes a bulk ingest actually uses, in pipeline order so the display
# reads as a flow rather than an alphabetised list.
QUEUES = ("extraction", "correlation", "hydration", "pattern", "evaluation", "sync", "default")

# Below this, a queue is not meaningfully backlogged and its growth is noise.
BACKLOG_ALERT_DEPTH = 500


async def _queue_depths() -> tuple[dict[str, int], int]:
    """(depth per lane, tasks held in-flight). Never raises — a broker
    hiccup must not 500 the page.

    In-flight is the broker's ``unacked`` hash: work DELIVERED to a
    worker but not finished — which includes every countdown/ETA task a
    worker holds in its heap. During the reconstruction phase of a bulk
    ingest this is where ALL the remaining work lives: 5,800 debounced
    reconstructs churned for hours while every queue read zero, and the
    page said "idle" about a pipeline burning a dollar a minute. HLEN is
    O(1), so this is as pollable as LLEN.
    """
    try:
        import redis.asyncio as aioredis

        from contextedge.config import settings

        client = aioredis.from_url(settings.celery_broker_url)
        try:
            depths = {q: int(await client.llen(q)) for q in QUEUES}
            in_flight = int(await client.hlen("unacked"))
            return depths, in_flight
        finally:
            await client.aclose()
    except Exception as exc:  # noqa: BLE001
        logger.warning("pipeline_health.queue_read_failed", error_type=type(exc).__name__)
        return {}, 0


async def get_pipeline_health(db: AsyncSession, tenant_id: uuid.UUID) -> dict[str, Any]:
    """A single read of everything an operator needs to judge a running ingest."""
    row = (
        await db.execute(
            text("""
            select
              (select count(*) from evidence_items where tenant_id = :t) as evidence,
              (select count(*) from evidence_items
                 where tenant_id = :t
                   and created_at > now() - interval '10 minutes') as evidence_10min,
              (select count(*) from evidence_items
                 where tenant_id = :t and embedding is not null) as embedded,
              (select count(*) from raw_evidence_objects where tenant_id = :t) as raw_objects,
              (select count(*) from canonical_identities
                 where tenant_id = :t and is_active) as identities,
              (select count(*) from case_links where tenant_id = :t) as case_links,
              -- The chain stage, and NOT case_links. Episode reconstruction is
              -- triggered by a newly created evidence<->evidence correlation
              -- edge; case links are an input to that, not the thing itself.
              -- Showing case_links here read as "1.3k correlations" while the
              -- quantity actually gating episodes was zero — a green number on
              -- the exact link that was broken.
              (select count(*) from correlation_edges
                 where tenant_id = :t) as correlation_edges,
              (select count(*) from episodes where tenant_id = :t) as episodes,
              (select count(*) from episodes
                 where tenant_id = :t
                   and created_at > now() - interval '10 minutes') as episodes_10min,
              (select count(*) from episodes
                 where tenant_id = :t
                   and reviewer_state = 'pending_review') as episodes_pending,
              (select count(*) from episodes
                 where tenant_id = :t
                   and reviewer_state = 'approved') as episodes_approved,
              (select count(*) from evidence_chunks
                 where tenant_id = :t) as chunks_total,
              (select count(*) from evidence_chunks
                 where tenant_id = :t and embedding is not null) as chunks_embedded,
              (select count(*) from patterns where tenant_id = :t) as patterns,
              (select count(*) from playbooks where tenant_id = :t) as playbooks
            """),
            {"t": str(tenant_id)},
        )
    ).mappings().one()

    latency = (
        await db.execute(
            text("""
            select
              count(*) as calls,
              coalesce(percentile_cont(0.5) within group (
                order by (payload->>'duration_ms')::int), 0)::int as p50_ms,
              coalesce(percentile_cont(0.95) within group (
                order by (payload->>'duration_ms')::int), 0)::int as p95_ms,
              coalesce(max((payload->>'duration_ms')::int), 0) as max_ms
            from operational_events
            where tenant_id = :t and event_type = 'llm.usage'
              and occurred_at > now() - interval '10 minutes'
            """),
            {"t": str(tenant_id)},
        )
    ).mappings().one()

    by_call = (
        await db.execute(
            text("""
            select payload->>'prompt_name' as call,
                   count(*) as calls,
                   coalesce(percentile_cont(0.5) within group (
                     order by (payload->>'duration_ms')::int), 0)::int as p50_ms,
                   coalesce(sum((payload->>'total_tokens')::bigint), 0) as tokens
            from operational_events
            where tenant_id = :t and event_type = 'llm.usage'
              and occurred_at > now() - interval '60 minutes'
              and payload->>'prompt_name' is not null
            group by 1 order by 4 desc
            """),
            {"t": str(tenant_id)},
        )
    ).mappings().all()

    # Burn rate at real model prices, so the page can project cost to
    # completion instead of leaving the operator to multiply in their head.
    # Grouped by model because the lanes run different models at different
    # rates (playbook on 3.7-flash, everything else on 2.5-flash).
    spend_rows = (
        await db.execute(
            text("""
            select payload->>'model' as model,
                   coalesce(sum((payload->>'prompt_tokens')::bigint), 0) as in_tok,
                   coalesce(sum((payload->>'completion_tokens')::bigint), 0) as out_tok
            from operational_events
            where tenant_id = :t and event_type = 'llm.usage'
              and occurred_at > now() - interval '60 minutes'
            group by 1
            """),
            {"t": str(tenant_id)},
        )
    ).mappings().all()
    from contextedge.services.admin_cost_service import _lookup_rate

    spend_last_hour_usd = 0.0
    for spend in spend_rows:
        rate = _lookup_rate(spend["model"] or "")
        spend_last_hour_usd += (
            spend["in_tok"] / 1_000_000 * rate["input"]
            + spend["out_tok"] / 1_000_000 * rate["output"]
        )

    queues, in_flight = await _queue_depths()
    counts = dict(row)

    # The graph chain, in order. The first zero is the diagnosis — everything
    # downstream of it is waiting on it, so naming that stage is more useful
    # than reporting five separate numbers and leaving the operator to infer
    # which one broke.
    chain = [
        ("evidence", counts["evidence"]),
        ("correlations", counts["correlation_edges"]),
        ("episodes", counts["episodes"]),
        ("patterns", counts["patterns"]),
        ("playbooks", counts["playbooks"]),
    ]
    stalled_at = next((name for name, n in chain if n == 0), None)

    alerts: list[dict[str, str]] = []
    if stalled_at and counts["evidence"] > 0:
        alerts.append({
            "level": "warning",
            "message": (
                f"The graph chain stops at '{stalled_at}': every stage after it "
                f"is waiting on work that has not been produced."
            ),
        })

    # The specific cause of a cold-start stall, named rather than left to be
    # rediscovered. Correlation's identity tier only trusts `resolved` /
    # `verified` identities, but every first-sighting identity is created
    # `provisional` and the only promotion path is human review. On a fresh
    # tenant that means the graph cannot begin forming on its own, and every
    # other number on this page looks healthy while it doesn't.
    if counts["correlation_edges"] == 0 and counts["identities"] > 0:
        trusted = (
            await db.execute(
                text("""
                select count(*) from canonical_identities
                where tenant_id = :t and is_active
                  and resolution_state in ('resolved', 'verified')
                """),
                {"t": str(tenant_id)},
            )
        ).scalar_one()
        if trusted == 0:
            alerts.append({
                "level": "warning",
                "message": (
                    f"None of the {counts['identities']:,} active identities are "
                    f"'resolved' or 'verified', and identity correlation only "
                    f"trusts those. New identities are created 'provisional', so "
                    f"until some are promoted no correlations form — and without "
                    f"correlations, no episodes."
                ),
            })
    backlog = queues.get("extraction", 0)
    if backlog > BACKLOG_ALERT_DEPTH:
        alerts.append({
            "level": "warning",
            "message": (
                f"{backlog:,} tasks queued on the extraction lane. Anything sharing "
                f"that lane waits behind all of them."
            ),
        })
    if counts["evidence_10min"] == 0 and backlog > 0:
        alerts.append({
            "level": "critical",
            "message": "No evidence produced in 10 minutes while work is still queued.",
        })
    # Empty queues with substantial in-flight work is the RECONSTRUCTION
    # phase, not idleness — say so, and name the number that proves the
    # pipeline is alive (episodes produced, since this phase produces
    # episodes, not evidence).
    if in_flight > 50 and sum(queues.values()) < 50:
        if counts["episodes_10min"] > 0:
            alerts.append({
                "level": "info",
                "message": (
                    f"{in_flight:,} tasks are held in-flight by workers (debounced "
                    f"reconstructions and other ETA holds). Queues reading empty "
                    f"does not mean idle: {counts['episodes_10min']:,} episodes were "
                    f"produced in the last 10 minutes."
                ),
            })
        elif counts["evidence_10min"] == 0:
            alerts.append({
                "level": "critical",
                "message": (
                    f"{in_flight:,} tasks are held in-flight but nothing — no "
                    f"evidence, no episodes — was produced in 10 minutes. The "
                    f"holding workers may be dead; their work will not resume on "
                    f"its own."
                ),
            })
    if counts["evidence"] and counts["embedded"] < counts["evidence"] * 0.9:
        alerts.append({
            "level": "info",
            "message": (
                f"{counts['evidence'] - counts['embedded']:,} evidence items are not "
                f"embedded yet, so they are not retrievable by vector search."
            ),
        })

    return {
        "counts": counts,
        "throughput_per_10min": counts["evidence_10min"],
        "episodes_per_10min": counts["episodes_10min"],
        "in_flight": in_flight,
        "spend_last_hour_usd": round(spend_last_hour_usd, 2),
        "queues": queues,
        "latency_10min": dict(latency),
        "by_call_60min": [dict(r) for r in by_call],
        "graph_chain": [{"stage": n, "count": c} for n, c in chain],
        "stalled_at": stalled_at,
        "alerts": alerts,
    }
