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
    """Idempotent sweep: recompute co-occurring CI pairs and ensure the
    symmetric edge (stored once, a < b ordering) with refreshed
    confidence. ensure_edge upserts, so growth in shared cases raises
    confidence on the same edge instead of duplicating it."""
    from contextedge.graph.builder import ensure_edge

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
    counts = {"pairs": len(rows), "edges": 0}
    for ci_a, ci_b, shared in rows:
        await ensure_edge(
            db,
            tenant_id,
            source_type="entity",
            source_id=ci_a,
            target_type="entity",
            target_id=ci_b,
            edge_type="co_fails_with",
            weight=1.0,
            confidence=pair_confidence(int(shared)),
            metadata={
                "origin": "co_occurrence",
                "shared_cases": int(shared),
                "symmetric": True,
            },
        )
        counts["edges"] += 1
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
    telemetry on this CI" without new connectors."""
    from contextedge.models.entity import Entity

    rows = (
        await db.execute(
            _MONITOR_SQL,
            {"tenant_id": tenant_id, "alert_types": list(_ALERT_EVIDENCE_TYPES)},
        )
    ).all()
    updated = 0
    for ci_id, sources in rows:
        entity = await db.get(Entity, ci_id)
        if entity is None or entity.tenant_id != tenant_id:
            continue
        attributes = dict(entity.attributes or {})
        merged = sorted(set(attributes.get("monitoring_sources") or []) | set(sources))
        if attributes.get("monitoring_sources") != merged:
            attributes["monitoring_sources"] = merged
            entity.attributes = attributes
            updated += 1
    if updated:
        await db.flush()
    return updated
