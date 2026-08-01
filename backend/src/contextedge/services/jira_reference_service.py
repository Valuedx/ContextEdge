"""Deterministic graph structure from Jira SM issue relationships.

The ServiceNow Phase 1 pattern applied to what Jira Service Management
exposes universally: issue links (Causes / Relates / Duplicate — with
the linked issue's type embedded in the API response), the parent
issue, project components, and optionally the JSM affected-services
custom field. The connector slims these into the payload; this module
turns them into:

- **Case-link keys**: linked issue keys join the ``jira_sm`` namespace
  alongside each issue's own key, so incident↔problem↔change
  correlation is symmetric and ordering-independent — same contract as
  the ServiceNow sys_id keys. Components/services are deliberately NOT
  case-link keys (shared modules would mass-merge unrelated cases —
  the same guard, third system).
- **Typed evidence→evidence edges**: "is caused by" a Change becomes
  ``caused_by_change`` — the same edge type ServiceNow emits, so
  change-risk assessment counts Jira changes with zero new code.
  Symmetric link types emit from ONE side only (each issue's payload
  carries both directions of every link; emitting both would double
  every edge).
- **Entities**: components (and configured affected services) become
  ``business_service`` entities with ``affects_ci`` edges — again the
  ServiceNow vocabulary, so post-action verification and change-risk
  blast-radius logic light up for Jira-anchored sessions too.

Not available here, honestly: no assignment-group equivalent (Jira
assigns people, and person entities never correlate by design), no
alert stream (JSM Operations alerts live in the Opsgenie-heritage API),
no topology (Assets is Premium + a separate API).
"""

from __future__ import annotations

import re
import uuid

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.graph.builder import ensure_edge
from contextedge.models.evidence import EvidenceItem, RawEvidenceObject
from contextedge.models.session import CaseLink
from contextedge.models.source import Source
from contextedge.services.servicenow_reference_service import (
    EntityReference,
    _ensure_entity,
)

logger = structlog.get_logger()

# Jira issue keys: PROJECT-123. Project keys are 2–10 uppercase
# alphanumerics starting with a letter. Anything else must never become
# a case-link key or a sysparm-style query fragment.
_ISSUE_KEY_RE = re.compile(r"[A-Z][A-Z0-9]{1,9}-\d{1,10}")

MAX_ISSUE_REFS = 20
MAX_ENTITY_REFS = 10
REVERSE_HEAL_MAX_SIBLINGS = 25

CHANGE_ISSUE_TYPES = {"change", "[system] change"}
PROBLEM_ISSUE_TYPES = {"problem", "[system] problem"}


def _valid_issue_key(value: object) -> str | None:
    if isinstance(value, str):
        candidate = value.strip().upper()
        if _ISSUE_KEY_RE.fullmatch(candidate):
            return candidate
    return None


def extract_issue_references(payload: dict | None) -> list[tuple[str, str]]:
    """``(edge_type, linked issue key)`` pairs, emitted from one side of
    each symmetric link so ensure_edge never doubles a relationship:

    - inward "is caused by" → ``caused_by_change`` when the linked issue
      is a Change (the change-risk service counts exactly this edge
      type), otherwise ``caused_by_issue``.
    - outward "causes" → skipped: the other issue emits its own
      caused_by when it syncs, and its payload carries the link too.
    - "duplicates" (outward) → ``duplicate_of``; the mirrored
      "is duplicated by" side is skipped.
    - "relates to" → ``related_problem`` only when the linked issue is a
      Problem and this issue is not (the incident side emits) — generic
      relates-to links are too noisy to type.
    - parent → ``child_of_issue``.
    """
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    p = payload or {}

    def add(edge_type: str, raw_key: object) -> None:
        key = _valid_issue_key(raw_key)
        if key is not None and key not in seen and len(out) < MAX_ISSUE_REFS:
            seen.add(key)
            out.append((edge_type, key))

    my_kind = str(p.get("record_kind") or "").lower()
    for link in p.get("issue_links") or []:
        if not isinstance(link, dict):
            continue
        description = str(link.get("description") or "").strip().lower()
        direction = link.get("direction")
        linked_type = str(link.get("issue_type") or "").strip().lower()
        if direction == "inward" and description == "is caused by":
            if linked_type in CHANGE_ISSUE_TYPES:
                add("caused_by_change", link.get("key"))
            else:
                add("caused_by_issue", link.get("key"))
        elif direction == "outward" and description == "duplicates":
            add("duplicate_of", link.get("key"))
        elif description == "relates to":
            if linked_type in PROBLEM_ISSUE_TYPES and my_kind != "problem":
                add("related_problem", link.get("key"))

    add("child_of_issue", p.get("parent_key"))
    return out


def extract_entity_references(payload: dict | None) -> list[EntityReference]:
    """Components and (when configured) JSM affected services as
    ``business_service`` entities. External ids are namespaced so a
    component id can never collide with a service id."""
    refs: list[EntityReference] = []
    p = payload or {}
    project = str(p.get("key") or "").split("-", 1)[0]

    for component in (p.get("components") or [])[:MAX_ENTITY_REFS]:
        if not isinstance(component, dict) or not component.get("name"):
            continue
        component_id = component.get("id") or component["name"]
        refs.append(
            EntityReference(
                sys_id=f"component:{project}:{component_id}",
                name=str(component["name"]),
                entity_type="business_service",
                edge_type="affects_ci",
                attributes={"source_kind": "jira_component", "project": project},
            )
        )

    for service in (p.get("affected_services") or [])[:MAX_ENTITY_REFS]:
        if not isinstance(service, dict) or not service.get("name"):
            continue
        refs.append(
            EntityReference(
                sys_id=f"service:{service.get('id') or service['name']}",
                name=str(service["name"]),
                entity_type="business_service",
                edge_type="affects_ci",
                attributes={"source_kind": "jsm_service"},
            )
        )
    return refs


async def _resolve_evidence_for_issue_key(
    db: AsyncSession, tenant_id: uuid.UUID, issue_key: str
) -> uuid.UUID | None:
    """Newest evidence for the Jira issue with this key, scoped to
    jira_sm sources so another connector's external id can never
    resolve here (mirror of the ServiceNow source scoping)."""
    return (
        await db.execute(
            select(EvidenceItem.id)
            .join(RawEvidenceObject, EvidenceItem.raw_object_ref == RawEvidenceObject.id)
            .join(Source, RawEvidenceObject.source_id == Source.id)
            .where(
                EvidenceItem.tenant_id == tenant_id,
                RawEvidenceObject.tenant_id == tenant_id,
                RawEvidenceObject.external_id == issue_key,
                Source.source_type == "jira_sm",
            )
            .order_by(EvidenceItem.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def heal_reverse_references(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    evidence: EvidenceItem,
    own_key: str,
) -> int:
    """Typed edges from issues that referenced *this* issue before it was
    ingested — found via their case-link rows on our key, payloads
    re-read to recover the edge type and direction. Same anchor
    limitation as ServiceNow: covers the first referencer; later ones
    heal on their own next update."""
    from contextedge.services.artifact_extraction_service import load_raw_payload

    if _valid_issue_key(own_key) is None:
        return 0

    sibling_rows = (
        await db.execute(
            select(CaseLink.evidence_id)
            .where(
                CaseLink.tenant_id == tenant_id,
                CaseLink.system == "jira_sm",
                CaseLink.external_id == own_key,
                CaseLink.evidence_id.is_not(None),
                CaseLink.evidence_id != evidence.id,
            )
            .limit(REVERSE_HEAL_MAX_SIBLINGS)
        )
    ).scalars().all()

    healed = 0
    for sibling_evidence_id in sibling_rows:
        try:
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
                for edge_type, key in extract_issue_references(payload):
                    if key != own_key:
                        continue
                    await ensure_edge(
                        db,
                        tenant_id,
                        "evidence",
                        sibling.id,
                        "evidence",
                        evidence.id,
                        edge_type,
                        metadata={"origin": "jira_reference", "healed": True},
                        domain_id=sibling.domain_id,
                    )
                    healed += 1
        except Exception as exc:
            logger.warning(
                "jira_reference.reverse_heal_skipped",
                tenant_id=str(tenant_id),
                sibling_evidence_id=str(sibling_evidence_id),
                error_type=type(exc).__name__,
                error=str(exc),
            )
    return healed


async def process_jira_references(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    evidence: EvidenceItem,
    payload: dict,
    own_key: str | None = None,
) -> dict:
    """Materialize typed edges and entities for one Jira evidence item.
    Idempotent; safe on every re-delivery. Called inside the correlation
    hook's SAVEPOINT — same containment as the ServiceNow path."""
    counts: dict = {
        "task_edges": 0,
        "entity_edges": 0,
        "unresolved_refs": 0,
        "healed_edges": 0,
    }

    for edge_type, issue_key in extract_issue_references(payload):
        target_id = await _resolve_evidence_for_issue_key(db, tenant_id, issue_key)
        if target_id is None:
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
            metadata={"origin": "jira_reference"},
            domain_id=evidence.domain_id,
        )
        counts["task_edges"] += 1

    for ref in extract_entity_references(payload):
        entity = await _ensure_entity(db, tenant_id, ref, external_system="jira_sm")
        await ensure_edge(
            db,
            tenant_id,
            "evidence",
            evidence.id,
            "entity",
            entity.id,
            ref.edge_type,
            metadata={"origin": "jira_reference"},
            domain_id=evidence.domain_id,
        )
        counts["entity_edges"] += 1

    if own_key is not None:
        counts["healed_edges"] = await heal_reverse_references(
            db, tenant_id, evidence, own_key
        )
    return counts
