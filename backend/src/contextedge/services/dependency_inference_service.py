"""CI relatedness inferred from incident co-occurrence (blueprint §1.5).

"CIs repeatedly failing together are related" — the blueprint's own
recipe for auto-constructing dependency signal where no CMDB service
map exists. Pairs of CI entities whose evidence shares canonical cases
across MULTIPLE distinct cases gain a symmetric ``co_fails_with`` edge.

Deliberately NOT ``depends_on``: co-occurrence carries no direction and
must never masquerade as authored topology. The edge starts at low
confidence (scaled by case count, capped), carries its derivation in
metadata, and the projection can rank it below CMDB-sourced edges.
One shared case is coincidence; the threshold is 3.
"""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy import text

logger = structlog.get_logger()

MIN_SHARED_CASES = 3
MAX_PAIRS_PER_RUN = 200
CONFIDENCE_BASE = 0.3
CONFIDENCE_PER_CASE = 0.1
CONFIDENCE_CAP = 0.7

_PAIR_SQL = text(
    """
    WITH ci_case AS (
        SELECT DISTINCT ge.target_node_id AS ci, m.canonical_case_id AS case_id
        FROM graph_edges ge
        JOIN evidence_case_memberships m ON m.evidence_id = ge.source_node_id
        WHERE ge.tenant_id = :tenant_id
          AND ge.edge_type = 'affects_ci'
          AND ge.valid_to IS NULL
          AND m.status = 'active'
    )
    SELECT a.ci AS ci_a, b.ci AS ci_b, count(*) AS shared_cases
    FROM ci_case a
    JOIN ci_case b ON a.case_id = b.case_id AND a.ci < b.ci
    GROUP BY 1, 2
    HAVING count(*) >= :min_cases
    ORDER BY count(*) DESC
    LIMIT :max_pairs
    """
)


def pair_confidence(shared_cases: int) -> float:
    return min(
        CONFIDENCE_BASE + CONFIDENCE_PER_CASE * (shared_cases - MIN_SHARED_CASES),
        CONFIDENCE_CAP,
    )


async def infer_co_failure_edges(db, tenant_id: uuid.UUID) -> dict:
    """Idempotent sweep: recompute co-occurring CI pairs (stored once,
    a < b ordering), refresh confidence on edges whose case counts
    changed, and CLOSE edges for pairs that no longer meet the
    threshold. The refresh happens here, not in ``ensure_edge`` —
    ensure_edge returns an existing active edge untouched, so relying
    on it froze every edge's confidence at first insert."""
    from datetime import UTC, datetime

    from sqlalchemy import select

    from contextedge.graph.builder import ensure_edge
    from contextedge.models.pattern import GraphEdge

    rows = (
        await db.execute(
            _PAIR_SQL,
            {
                "tenant_id": tenant_id,
                "min_cases": MIN_SHARED_CASES,
                "max_pairs": MAX_PAIRS_PER_RUN,
            },
        )
    ).all()
    computed = {(ci_a, ci_b): int(shared) for ci_a, ci_b, shared in rows}

    existing_res = await db.execute(
        select(GraphEdge).where(
            GraphEdge.tenant_id == tenant_id,
            GraphEdge.edge_type == "co_fails_with",
            GraphEdge.valid_to.is_(None),
        )
    )
    existing = {
        (e.source_node_id, e.target_node_id): e
        for e in existing_res.scalars().all()
    }

    counts = {"pairs": len(rows), "edges": 0, "refreshed": 0, "expired": 0}
    for (ci_a, ci_b), shared in computed.items():
        confidence = pair_confidence(shared)
        edge = existing.get((ci_a, ci_b))
        if edge is not None:
            meta = dict(edge.metadata_extra or {})
            if edge.confidence != confidence or meta.get("shared_cases") != shared:
                edge.confidence = confidence
                meta["shared_cases"] = shared
                edge.metadata_extra = meta
                counts["refreshed"] += 1
            continue
        await ensure_edge(
            db,
            tenant_id,
            source_type="entity",
            source_id=ci_a,
            target_type="entity",
            target_id=ci_b,
            edge_type="co_fails_with",
            weight=1.0,
            confidence=confidence,
            metadata={
                "origin": "co_occurrence",
                "shared_cases": shared,
                "symmetric": True,
            },
        )
        counts["edges"] += 1

    # Expire (never hard-delete) inferred edges whose pair dropped below
    # the threshold — e.g. after case memberships are corrected. Only
    # safe when the pair query was NOT truncated: with a truncated
    # result, an absent pair may simply be past the LIMIT, and expiring
    # it would flap the edge on every sweep.
    if len(rows) < MAX_PAIRS_PER_RUN:
        now = datetime.now(UTC)
        for key, edge in existing.items():
            if key in computed:
                continue
            if (edge.metadata_extra or {}).get("origin") != "co_occurrence":
                continue  # not ours to expire
            edge.valid_to = now
            counts["expired"] += 1
    else:
        logger.warning(
            "dependency_inference.expiry_skipped_truncated",
            tenant_id=str(tenant_id),
            max_pairs=MAX_PAIRS_PER_RUN,
        )

    logger.info(
        "dependency_inference.swept", tenant_id=str(tenant_id), **counts
    )
    counts["monitored_cis"] = await index_monitoring_sources(db, tenant_id)
    return counts


# Alert-shaped evidence types whose affects_ci link tells us "this CI
# has coverage from that source" — the blueprint's telemetry index
# (layer 5), derived from data already ingested. Stored as an entity
# ATTRIBUTE rather than edges to a monitoring-source node: a source
# node would be a hub with every covered CI one hop away, exactly the
# fan-out shape the projection budget dies on.
_ALERT_EVIDENCE_TYPES = ("alert", "alert_rollup", "em_alert", "splunk_log", "event")

_MONITOR_SQL = text(
    """
    SELECT ge.target_node_id AS ci, array_agg(DISTINCT e.source_type) AS sources
    FROM graph_edges ge
    JOIN evidence_items e ON e.id = ge.source_node_id
    WHERE ge.tenant_id = :tenant_id
      AND ge.edge_type = 'affects_ci'
      AND ge.valid_to IS NULL
      AND e.evidence_type = ANY(:alert_types)
      AND e.source_type IS NOT NULL
    GROUP BY 1
    """
)


async def index_monitoring_sources(db, tenant_id: uuid.UUID) -> int:
    """Stamp each CI's observed monitoring coverage into its attributes
    (projected as an entity fact), answering "where can I look for
    telemetry on this CI" without new connectors.

    RECONCILED, not unioned: the attribute mirrors what the active
    alert-shaped evidence currently shows. A source that stops covering
    a CI (or whose evidence is corrected/expired) drops off — a stale
    "look here for telemetry" pointer misleads exactly when it is
    needed. CIs absent from the query entirely get the attribute
    cleared for the same reason."""
    from sqlalchemy import select

    from contextedge.models.entity import Entity

    rows = (
        await db.execute(
            _MONITOR_SQL,
            {"tenant_id": tenant_id, "alert_types": list(_ALERT_EVIDENCE_TYPES)},
        )
    ).all()
    observed = {ci_id: sorted(set(sources)) for ci_id, sources in rows}
    updated = 0
    for ci_id, sources in observed.items():
        entity = await db.get(Entity, ci_id)
        if entity is None or entity.tenant_id != tenant_id:
            continue
        attributes = dict(entity.attributes or {})
        if attributes.get("monitoring_sources") != sources:
            attributes["monitoring_sources"] = sources
            entity.attributes = attributes
            updated += 1

    # Clear coverage on CIs no longer observed at all.
    stale_res = await db.execute(
        select(Entity).where(
            Entity.tenant_id == tenant_id,
            Entity.attributes.has_key("monitoring_sources"),
        )
    )
    for entity in stale_res.scalars().all():
        if entity.id in observed:
            continue
        attributes = dict(entity.attributes or {})
        attributes.pop("monitoring_sources", None)
        entity.attributes = attributes
        updated += 1

    if updated:
        await db.flush()
    return updated
