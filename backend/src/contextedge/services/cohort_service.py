"""Cohort shared-attribute analysis (blueprint §1.6 primitive 2).

"Who else, and what do they share?" — given the evidence of a set of
similar incidents, find what the affected CIs have in common. A shared
attribute (same model, same OS build, same class, same owning group)
localizes the cause to that layer: the VLAN-42 / driver-ring move.

Pure SQL + counting over structures that already exist: affects_ci
edges, entity trait columns, entity attributes, assigned_to_group
edges. No LLM.
"""

from __future__ import annotations

import uuid
from collections import Counter

import structlog
from sqlalchemy import select

from contextedge.models.entity import Entity
from contextedge.models.pattern import GraphEdge

logger = structlog.get_logger()

# Attribute dimensions examined, in presentation order. Column
# dimensions read Entity columns; attr dimensions read the JSONB.
# entity_type is deliberately absent: every CI in an affects_ci cohort
# shares it trivially, and a 100%-coverage non-signal would outrank
# every real discriminator.
_COLUMN_DIMENSIONS = (
    "environment",
    "manufacturer",
    "model",
    "os_name",
    "os_version",
)
_ATTR_DIMENSIONS = ("ci_class", "criticality", "support_group")

MIN_COHORT = 3
MIN_COVERAGE = 0.6


async def get_cohort_shared_attributes(
    db,
    tenant_id: uuid.UUID,
    evidence_ids: list[uuid.UUID],
) -> dict:
    """Shared attributes across the CIs of a cohort of evidence.

    Returns {cohort_size, ci_count, shared: [{dimension, value,
    coverage, count}]} — dimensions where one value covers >=60% of a
    cohort of >=3 CIs, ranked by coverage. Below the floor, the honest
    answer is an empty list, never a stretched pattern.
    """
    out: dict = {"cohort_size": len(evidence_ids), "ci_count": 0, "shared": []}
    if not evidence_ids:
        return out

    ci_ids = (
        (
            await db.execute(
                select(GraphEdge.target_node_id)
                .distinct()
                .where(
                    GraphEdge.tenant_id == tenant_id,
                    GraphEdge.edge_type == "affects_ci",
                    GraphEdge.valid_to.is_(None),
                    GraphEdge.source_node_id.in_(evidence_ids[:200]),
                )
            )
        )
        .scalars()
        .all()
    )
    if not ci_ids:
        return out
    entities = (
        (
            await db.execute(
                select(Entity).where(
                    Entity.tenant_id == tenant_id, Entity.id.in_(ci_ids)
                )
            )
        )
        .scalars()
        .all()
    )
    out["ci_count"] = len(entities)
    if len(entities) < MIN_COHORT:
        return out

    for dimension in _COLUMN_DIMENSIONS + _ATTR_DIMENSIONS:
        values: Counter = Counter()
        for entity in entities:
            if dimension in _ATTR_DIMENSIONS:
                value = (entity.attributes or {}).get(dimension)
            else:
                value = getattr(entity, dimension, None)
            if value:
                values[str(value)] += 1
        if not values:
            continue
        top_value, count = values.most_common(1)[0]
        coverage = count / len(entities)
        if coverage >= MIN_COVERAGE and count >= MIN_COHORT:
            out["shared"].append(
                {
                    "dimension": dimension,
                    "value": top_value,
                    "coverage": round(coverage, 2),
                    "count": count,
                }
            )
    out["shared"].sort(key=lambda s: -s["coverage"])
    return out
