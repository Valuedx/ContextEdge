"""Change-risk assessment from operational history (Phase 4).

Answers "what does history say about changing this CI?" before a change
is approved — deterministically, from structure Phases 1–3 already
materialized. No LLM call, no ServiceNow round-trip: every number is
explainable and every factor names its source.

Signals, per CI (window-bounded):

- **Change→incident history** — distinct change records on the CI
  (``affects_ci`` edges, record kind discriminated by the thread id
  prefix the connector writes) versus those that are targets of a
  ``caused_by_change`` edge (a human wrote that reference on the
  incident: deterministic, not inferred).
- **Incident pressure** — distinct incident records on the CI in the
  window; a chronically noisy CI makes any change riskier.
- **Alert activity** — distinct per-day alert-rollup threads (Phase 3)
  on the CI: active telemetry trouble right now.
- **Blast radius** — entities that depend on / run on / are hosted on
  the CI, from the Phase 2 cached topology. Explicitly labeled as the
  cached working set, never presented as the full CMDB.

The risk level is a transparent additive score over those factors — the
``factors`` list in the result is the explanation, one sentence per
contributing signal.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.models.entity import Entity
from contextedge.models.evidence import EvidenceItem, Thread
from contextedge.models.pattern import GraphEdge

logger = structlog.get_logger()

DEFAULT_WINDOW_DAYS = 180
MAX_WINDOW_DAYS = 730
# affects_ci evidence considered per assessment — a chronically busy CI
# is bounded, not scanned exhaustively (newest first, see query).
EVIDENCE_SCAN_CAP = 2_000
DEPENDENT_SAMPLE_CAP = 10

# Topology edge types that mean "the source needs the target": the
# sources of these edges pointing AT the assessed CI are its blast
# radius. "contains" is composition, not dependency, and is excluded.
DEPENDENCY_EDGE_TYPES = ("depends_on", "runs_on", "hosted_on", "uses")

RATE_HIGH_THRESHOLD = 0.3
DEPENDENTS_SIGNIFICANT = 5
INCIDENTS_NOISY = 5
ALERT_DAYS_ACTIVE = 2


async def _ci_evidence_by_kind(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    entity_id: uuid.UUID,
    cutoff: datetime,
) -> dict[str, dict[str, set[uuid.UUID]]]:
    """Evidence touching the CI, grouped by record kind (thread prefix)
    → {kind: {thread_key: {evidence ids}}}. Distinct thread = distinct
    upstream record; multiple evidence rows per thread are versions."""
    rows = (
        await db.execute(
            select(EvidenceItem.id, Thread.external_thread_id)
            .join(
                GraphEdge,
                (GraphEdge.source_node_type == "evidence")
                & (GraphEdge.source_node_id == EvidenceItem.id),
            )
            .join(Thread, EvidenceItem.thread_id == Thread.id)
            .where(
                GraphEdge.tenant_id == tenant_id,
                GraphEdge.edge_type == "affects_ci",
                GraphEdge.target_node_type == "entity",
                GraphEdge.target_node_id == entity_id,
                GraphEdge.valid_to.is_(None),
                EvidenceItem.tenant_id == tenant_id,
                func.coalesce(EvidenceItem.created_at_source, EvidenceItem.created_at)
                >= cutoff,
            )
            .order_by(EvidenceItem.created_at.desc())
            .limit(EVIDENCE_SCAN_CAP)
        )
    ).all()

    by_kind: dict[str, dict[str, set[uuid.UUID]]] = {}
    for evidence_id, thread_key in rows:
        kind = (thread_key or "").split(":", 1)[0]
        by_kind.setdefault(kind, {}).setdefault(thread_key, set()).add(evidence_id)
    return by_kind


async def _incident_causing_change_threads(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    change_threads: dict[str, set[uuid.UUID]],
) -> set[str]:
    """Change threads on the CI whose evidence is the target of a
    caused_by_change edge — a human blamed an incident on that change."""
    evidence_to_thread: dict[uuid.UUID, str] = {}
    for thread_key, evidence_ids in change_threads.items():
        for evidence_id in evidence_ids:
            evidence_to_thread[evidence_id] = thread_key
    if not evidence_to_thread:
        return set()

    blamed = (
        await db.execute(
            select(GraphEdge.target_node_id).where(
                GraphEdge.tenant_id == tenant_id,
                GraphEdge.edge_type == "caused_by_change",
                GraphEdge.target_node_type == "evidence",
                GraphEdge.target_node_id.in_(tuple(evidence_to_thread)),
                GraphEdge.valid_to.is_(None),
            )
        )
    ).scalars().all()
    return {evidence_to_thread[evidence_id] for evidence_id in blamed}


async def _cached_dependents(
    db: AsyncSession, tenant_id: uuid.UUID, entity_id: uuid.UUID
) -> list[Entity]:
    dependent_ids = (
        await db.execute(
            select(GraphEdge.source_node_id)
            .where(
                GraphEdge.tenant_id == tenant_id,
                GraphEdge.edge_type.in_(DEPENDENCY_EDGE_TYPES),
                GraphEdge.source_node_type == "entity",
                GraphEdge.target_node_type == "entity",
                GraphEdge.target_node_id == entity_id,
                GraphEdge.valid_to.is_(None),
            )
            .distinct()
            .limit(500)
        )
    ).scalars().all()
    if not dependent_ids:
        return []
    return list(
        (
            await db.execute(
                select(Entity)
                .where(
                    Entity.tenant_id == tenant_id,
                    Entity.id.in_(tuple(dependent_ids)),
                )
                .order_by(Entity.name)
            )
        ).scalars().all()
    )


def _score(
    *,
    changes: int,
    blamed_changes: int,
    incidents: int,
    alert_days: int,
    dependents: int,
) -> tuple[str, list[str]]:
    """Transparent additive scoring; the factors ARE the explanation."""
    score = 0
    factors: list[str] = []

    if blamed_changes:
        rate = blamed_changes / max(changes, 1)
        if rate >= RATE_HIGH_THRESHOLD:
            score += 2
        else:
            score += 1
        factors.append(
            f"{blamed_changes} of {changes} changes on this CI in the window "
            f"were blamed for incidents (caused_by references)"
        )
    if incidents >= INCIDENTS_NOISY:
        score += 1
        factors.append(
            f"{incidents} incidents touched this CI in the window — "
            "chronically noisy CIs make any change riskier"
        )
    if alert_days >= ALERT_DAYS_ACTIVE:
        score += 1
        factors.append(
            f"alert activity on {alert_days} separate days in the window "
            "(telemetry trouble is already present)"
        )
    if dependents >= DEPENDENTS_SIGNIFICANT:
        score += 1
        factors.append(
            f"{dependents} cached dependents — a failure here propagates"
        )

    if score >= 3:
        return "high", factors
    if score >= 1:
        return "medium", factors
    factors.append("no adverse history for this CI in the window")
    return "low", factors


async def assess_change_risk(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    ci_term: str,
    *,
    window_days: int = DEFAULT_WINDOW_DAYS,
) -> dict:
    """Deterministic change-risk profile for a CI (name or sys_id)."""
    from contextedge.services.cmdb_topology_service import resolve_ci_entity

    ci_term = (ci_term or "").strip()
    if not ci_term:
        return {"error": {"code": "invalid_ci", "message": "Provide a CI name or sys_id."}}
    entity = await resolve_ci_entity(db, tenant_id, ci_term)
    if entity is None:
        return {
            "error": {
                "code": "unknown_ci",
                "message": (
                    f"No CI matching '{ci_term}' is known. Risk assessment "
                    "needs the CI's operational history — ingest tickets "
                    "referencing it (or fetch it via cmdb_topology) first."
                ),
            }
        }

    window_days = min(max(int(window_days), 1), MAX_WINDOW_DAYS)
    cutoff = datetime.now(UTC) - timedelta(days=window_days)

    by_kind = await _ci_evidence_by_kind(db, tenant_id, entity.id, cutoff)
    change_threads = by_kind.get("change_request", {})
    incident_threads = by_kind.get("incident", {})
    alert_threads = by_kind.get("em_alert_rollup", {})

    blamed = await _incident_causing_change_threads(db, tenant_id, change_threads)
    dependents = await _cached_dependents(db, tenant_id, entity.id)

    changes = len(change_threads)
    incidents = len(incident_threads)
    alert_days = len(alert_threads)  # one rollup thread per (CI, day)

    risk_level, factors = _score(
        changes=changes,
        blamed_changes=len(blamed),
        incidents=incidents,
        alert_days=alert_days,
        dependents=len(dependents),
    )

    return {
        "ci": {
            "name": entity.name,
            "sys_id": entity.external_id,
            "ci_class": (entity.attributes or {}).get("ci_class"),
        },
        "window_days": window_days,
        "changes_on_ci": changes,
        "incident_causing_changes": len(blamed),
        "change_incident_rate": round(len(blamed) / changes, 3) if changes else None,
        "incidents_on_ci": incidents,
        "alert_activity_days": alert_days,
        "dependents_cached": len(dependents),
        "dependent_names": [d.name for d in dependents[:DEPENDENT_SAMPLE_CAP]],
        "risk_level": risk_level,
        "factors": factors,
        # Honesty about coverage: dependents come from the demand-driven
        # topology cache (Phase 2), not the full CMDB.
        "topology_note": (
            "dependents reflect the cached topology working set"
            + (
                f" (CI topology as of {entity.last_synced_at.isoformat()})"
                if entity.last_synced_at
                else " (CI topology never fetched — call cmdb_topology to warm it)"
            )
        ),
    }
