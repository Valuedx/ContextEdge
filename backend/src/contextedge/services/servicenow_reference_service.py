"""Deterministic graph structure from ServiceNow reference fields (Phase 1).

ServiceNow task records carry human-verified pointers the connector
previously discarded: ``incident.problem_id`` (root-cause problem),
``incident.caused_by`` / ``rfc`` (the change that caused / remediates it),
``parent_incident`` (major-incident membership), plus the affected CI
(``cmdb_ci``) and owning team (``assignment_group``). This module turns
them into graph structure the agent can traverse:

- **Typed evidence→evidence edges** (``ensure_edge``, idempotent) so
  traversal can hop incident → problem → remediating change with the
  relationship *kind* preserved — stronger than the generic case-link
  correlation, which records only "same case".
- **Entity rows** for CIs and assignment groups on the
  ``(entity_type, external_system, external_id)`` natural key, plus
  evidence→entity edges. Seed resolution Layer C matches entities by
  exact name, so a query mentioning ``vpn-gw-east-01`` seeds the CI and
  traversal reaches every incident/change that touched it.

Case-link correlation (confidence 1.0) is handled by
``correlation_service.extract_case_link_candidates`` consuming
``extract_task_references``: the referenced sys_ids become symmetric
case-link keys, so incident↔problem↔change correlation is
ordering-independent even before both sides are ingested.

Merging semantics, stated explicitly: every incident pointing at the
same problem (or caused by the same change, or child of the same major
incident) joins ONE canonical case. That is intended — a problem record
*is* ServiceNow's human-verified grouping of incidents under a root
cause, so the canonical case here represents the root-cause cluster.
This differs deliberately from the identity tier, which must never merge
cases on shared people; these keys are engineer-set reference fields,
not co-occurrence heuristics. One known cosmetic nuance: when a record's
references span two pre-existing canonical cases, only the case-link
rows matching its own candidate keys re-anchor to the first case's id —
other rows keep theirs. ``canonical_case_id`` is an informational
grouping key with no downstream consumers; the pairwise correlation
edges are complete either way.

Ordering for the *typed* edges: the forward pass resolves targets that
already exist; ``heal_reverse_references`` covers the first record that
referenced us before we were ingested (found via its case-link row).
Later referencers heal on their own next update — ServiceNow tickets
re-deliver on every ``sys_updated_on`` touch, so the forward pass re-runs
for any record still receiving activity.

Best-effort throughout: called after correlation, never raises into the
caller — a failure here loses enrichment, not the correlation itself.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field

import structlog
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.graph.builder import ensure_edge
from contextedge.models.entity import Entity
from contextedge.models.evidence import EvidenceItem, RawEvidenceObject
from contextedge.models.session import CaseLink
from contextedge.models.source import Source

logger = structlog.get_logger()

# Task-to-task reference fields → graph edge types (edge direction is
# referencing record → referenced record).
TASK_REFERENCE_EDGE_TYPES = {
    "problem_id": "related_problem",
    "caused_by": "caused_by_change",
    "rfc": "remediated_by_change",
    "parent_incident": "child_of_incident",
}

# cmdb_ci sys_class_name → entities.entity_type. Anything unmapped is a
# generic configuration_item; the class is preserved in attributes so a
# later, richer mapping loses nothing.
CI_CLASS_ENTITY_TYPES = {
    "cmdb_ci_appl": "application",
    "cmdb_ci_database": "database",
    "cmdb_ci_db_instance": "database",
    "cmdb_ci_service": "business_service",
    "cmdb_ci_service_auto": "business_service",
}

# Every ServiceNow sys_id is exactly 32 lowercase hex chars. Rejecting
# anything else keeps display-value serializations ("PRB0004031", a CI's
# human name) from becoming junk case-link keys that can never match a
# record's real external_id.
_SYS_ID_RE = re.compile(r"[0-9a-f]{32}")

# Bound on how many case-link siblings the reverse-heal pass will load
# payloads for. Siblings beyond it heal via their own forward pass.
REVERSE_HEAL_MAX_SIBLINGS = 25


def _ref_sys_id(raw: object) -> str | None:
    """sys_id from a reference field in any serialization the Table API
    produces: ``{"value": ..., "link": ...}`` (display_value=false),
    ``{"display_value": ..., "value": ...}`` (display_value=all), or a
    plain string."""
    if isinstance(raw, dict):
        raw = raw.get("value")
    if isinstance(raw, str):
        candidate = raw.strip().lower()
        if _SYS_ID_RE.fullmatch(candidate):
            return candidate
    return None


def _display(raw: object) -> str | None:
    """Human-readable value from a plain or display_value=all field."""
    if isinstance(raw, dict):
        raw = raw.get("display_value") or raw.get("value")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None


def extract_task_references(payload: dict | None) -> list[tuple[str, str]]:
    """``(edge_type, referenced sys_id)`` pairs for task-to-task pointers.

    ``cmdb_ci`` / ``assignment_group`` are deliberately excluded from this
    (and therefore from case-link candidates): hundreds of records share
    one CI or team, and joining them as 1.0 case links would merge
    unrelated cases into one canonical case. Shared infrastructure goes
    through the entity path instead.
    """
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    p = payload or {}
    for field_name, edge_type in TASK_REFERENCE_EDGE_TYPES.items():
        sys_id = _ref_sys_id(p.get(field_name))
        if sys_id is not None and sys_id not in seen:
            seen.add(sys_id)
            out.append((edge_type, sys_id))
    return out


@dataclass
class EntityReference:
    sys_id: str
    name: str
    entity_type: str
    edge_type: str
    attributes: dict = field(default_factory=dict)


def extract_entity_references(payload: dict | None) -> list[EntityReference]:
    """CI and assignment-group references, with display names from the
    dot-walked fields the connector requests (``cmdb_ci.name`` etc.).
    Falls back to the sys_id as the name so the entity is still created
    (and later renamed in place) when dot-walk fields are absent."""
    refs: list[EntityReference] = []
    p = payload or {}

    ci_sys_id = _ref_sys_id(p.get("cmdb_ci"))
    if ci_sys_id is not None:
        ci_class = _display(p.get("cmdb_ci.sys_class_name")) or ""
        refs.append(
            EntityReference(
                sys_id=ci_sys_id,
                name=_display(p.get("cmdb_ci.name")) or ci_sys_id,
                entity_type=CI_CLASS_ENTITY_TYPES.get(ci_class, "configuration_item"),
                edge_type="affects_ci",
                attributes={"ci_class": ci_class} if ci_class else {},
            )
        )

    group_sys_id = _ref_sys_id(p.get("assignment_group"))
    if group_sys_id is not None:
        refs.append(
            EntityReference(
                sys_id=group_sys_id,
                name=_display(p.get("assignment_group.name")) or group_sys_id,
                entity_type="assignment_group",
                edge_type="assigned_to_group",
            )
        )
    return refs


async def _resolve_evidence_for_sys_id(
    db: AsyncSession, tenant_id: uuid.UUID, sys_id: str
) -> uuid.UUID | None:
    """Newest evidence item whose raw object is the ServiceNow record with
    this sys_id (updates create new content-hashed evidence per record;
    the newest is the current state). Scoped to ServiceNow sources — a
    32-hex external_id from another connector must never resolve here."""
    return (
        await db.execute(
            select(EvidenceItem.id)
            .join(RawEvidenceObject, EvidenceItem.raw_object_ref == RawEvidenceObject.id)
            .join(Source, RawEvidenceObject.source_id == Source.id)
            .where(
                EvidenceItem.tenant_id == tenant_id,
                RawEvidenceObject.tenant_id == tenant_id,
                RawEvidenceObject.external_id == sys_id,
                Source.source_type == "servicenow",
            )
            .order_by(EvidenceItem.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def _ensure_entity(
    db: AsyncSession, tenant_id: uuid.UUID, ref: EntityReference
) -> Entity:
    """Find-or-create on ``(external_system, external_id)`` — deliberately
    ignoring entity_type in the lookup so a CI whose class mapping changes
    later updates the one existing row instead of forking a duplicate."""
    existing = (
        await db.execute(
            select(Entity)
            .where(
                Entity.tenant_id == tenant_id,
                Entity.external_system == "servicenow",
                Entity.external_id == ref.sys_id,
            )
            .order_by(Entity.created_at)
            .limit(1)
        )
    ).scalar_one_or_none()
    if existing is not None:
        # Refresh the display name when upstream renamed the CI — but a
        # sys_id fallback name never overwrites a real name.
        if ref.name != ref.sys_id and existing.name != ref.name:
            existing.name = ref.name
        return existing

    entity = Entity(
        tenant_id=tenant_id,
        entity_type=ref.entity_type,
        external_system="servicenow",
        external_id=ref.sys_id,
        name=ref.name,
        attributes=ref.attributes,
        source_ref={"system": "servicenow", "sys_id": ref.sys_id},
        confidence=1.0,
    )
    try:
        async with db.begin_nested():
            db.add(entity)
            await db.flush()
        return entity
    except IntegrityError:
        # Concurrent correlate worker won the natural-key race.
        return (
            await db.execute(
                select(Entity).where(
                    Entity.tenant_id == tenant_id,
                    Entity.entity_type == ref.entity_type,
                    Entity.external_system == "servicenow",
                    Entity.external_id == ref.sys_id,
                )
            )
        ).scalar_one()


async def heal_reverse_references(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    evidence: EvidenceItem,
    own_sys_id: str,
) -> int:
    """Create typed edges from records that referenced *this* record before
    it was ingested.

    Their forward pass found no target then, but it did register a
    case-link row keyed on our sys_id — follow it back, re-read that
    sibling's payload, and materialize the edge in the correct direction
    (referencer → us). Payload loads are bounded; failures skip the row.
    """
    from contextedge.services.artifact_extraction_service import load_raw_payload

    sibling_rows = (
        await db.execute(
            select(CaseLink.evidence_id)
            .where(
                CaseLink.tenant_id == tenant_id,
                CaseLink.system == "servicenow",
                CaseLink.external_id == own_sys_id,
                CaseLink.evidence_id.is_not(None),
                CaseLink.evidence_id != evidence.id,
            )
            .limit(REVERSE_HEAL_MAX_SIBLINGS)
        )
    ).scalars().all()

    healed = 0
    for sibling_evidence_id in sibling_rows:
        try:
            # Per-sibling SAVEPOINT: a swallowed *database* error would
            # otherwise abort the whole session and every later statement
            # in this correlate pass would raise InFailedSQLTransaction.
            async with db.begin_nested():
                sibling = await db.get(EvidenceItem, sibling_evidence_id)
                if (
                    sibling is None
                    or sibling.tenant_id != tenant_id
                    or sibling.raw_object_ref is None
                ):
                    continue
                raw = await db.get(RawEvidenceObject, sibling.raw_object_ref)
                if raw is None:
                    continue
                payload = await load_raw_payload(raw)
                for edge_type, sys_id in extract_task_references(payload):
                    if sys_id != own_sys_id:
                        continue
                    await ensure_edge(
                        db,
                        tenant_id,
                        "evidence",
                        sibling.id,
                        "evidence",
                        evidence.id,
                        edge_type,
                        metadata={"origin": "servicenow_reference", "healed": True},
                        domain_id=sibling.domain_id,
                    )
                    healed += 1
        except Exception as exc:
            logger.warning(
                "servicenow_reference.reverse_heal_skipped",
                tenant_id=str(tenant_id),
                sibling_evidence_id=str(sibling_evidence_id),
                error_type=type(exc).__name__,
                error=str(exc),
            )
    return healed


async def process_servicenow_references(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    evidence: EvidenceItem,
    payload: dict,
    own_sys_id: str | None = None,
) -> dict:
    """Materialize typed edges and entities for one ServiceNow evidence
    item. Idempotent (ensure_edge / natural-key upsert); safe to re-run on
    every re-delivery of the record."""
    counts = {"task_edges": 0, "entity_edges": 0, "unresolved_refs": 0, "healed_edges": 0}

    for edge_type, sys_id in extract_task_references(payload):
        target_id = await _resolve_evidence_for_sys_id(db, tenant_id, sys_id)
        if target_id is None:
            # Not ingested yet. The case-link key already ties the pair;
            # the typed edge appears via heal_reverse_references when the
            # target arrives, or on our next re-delivery.
            counts["unresolved_refs"] += 1
            continue
        if target_id == evidence.id:
            continue
        await ensure_edge(
            db,
            tenant_id,
            "evidence",
            evidence.id,
            "evidence",
            target_id,
            edge_type,
            metadata={"origin": "servicenow_reference"},
            domain_id=evidence.domain_id,
        )
        counts["task_edges"] += 1

    for ref in extract_entity_references(payload):
        entity = await _ensure_entity(db, tenant_id, ref)
        await ensure_edge(
            db,
            tenant_id,
            "evidence",
            evidence.id,
            "entity",
            entity.id,
            ref.edge_type,
            metadata={"origin": "servicenow_reference"},
            domain_id=evidence.domain_id,
        )
        counts["entity_edges"] += 1

    if own_sys_id is not None:
        counts["healed_edges"] = await heal_reverse_references(
            db, tenant_id, evidence, own_sys_id
        )
    return counts
