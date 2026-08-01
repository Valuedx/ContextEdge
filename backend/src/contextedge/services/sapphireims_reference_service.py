"""Deterministic structure from SapphireIMS ticket payloads.

The Phase 1 pattern, third system. SapphireIMS payloads are already
normalized by the config-mapped connector, so extraction reads stable
keys regardless of instance field names:

- ``related_tickets`` → symmetric case-link keys in the ``sapphireims``
  namespace plus generic ``related_ticket`` evidence edges. SapphireIMS
  does not expose the *type* of a relation publicly, so edges stay
  untyped-generic rather than guessed — a wrong ``caused_by_change``
  would poison change-risk assessment, and no edge is better than a
  wrong one. The linked ticket's own record kind is still knowable from
  its thread prefix when both sides are ingested.
- ``ci_name`` → ``configuration_item`` entity; ``service_name`` →
  ``business_service`` entity — both with ``affects_ci`` edges
  (``external_system="sapphireims"`` via the generalized upsert), so
  seed resolution, change-risk, and post-action verification treat
  SapphireIMS anchors like every other source's.

Ticket ids are validated to a conservative shape (alphanumeric with
``-_/#`` separators, bounded length) — instance formats vary, but junk
must never become a case-link key.
"""

from __future__ import annotations

import re
import uuid

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.graph.builder import ensure_edge
from contextedge.models.evidence import EvidenceItem, RawEvidenceObject
from contextedge.models.source import Source
from contextedge.services.servicenow_reference_service import (
    EntityReference,
    _ensure_entity,
)

logger = structlog.get_logger()

_TICKET_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9\-_/#]{0,39}")
MAX_TICKET_REFS = 20
MAX_NAME_CHARS = 200


def _valid_ticket_id(value: object) -> str | None:
    if isinstance(value, str):
        candidate = value.strip()
        if candidate and _TICKET_ID_RE.fullmatch(candidate):
            return candidate
    return None


def extract_ticket_references(payload: dict | None) -> list[str]:
    """Validated related-ticket ids, deduplicated, order-preserving."""
    out: list[str] = []
    raw = (payload or {}).get("related_tickets")
    if not isinstance(raw, list):
        return out
    for item in raw:
        ticket_id = _valid_ticket_id(item)
        if ticket_id is not None and ticket_id not in out:
            out.append(ticket_id)
            if len(out) >= MAX_TICKET_REFS:
                break
    return out


def extract_entity_references(payload: dict | None) -> list[EntityReference]:
    refs: list[EntityReference] = []
    p = payload or {}

    ci_name = p.get("ci_name")
    if isinstance(ci_name, str) and ci_name.strip():
        name = ci_name.strip()[:MAX_NAME_CHARS]
        refs.append(
            EntityReference(
                sys_id=f"ci:{name.lower()}",
                name=name,
                entity_type="configuration_item",
                edge_type="affects_ci",
                attributes={"source_kind": "sapphireims_asset"},
            )
        )

    service_name = p.get("service_name")
    if isinstance(service_name, str) and service_name.strip():
        name = service_name.strip()[:MAX_NAME_CHARS]
        refs.append(
            EntityReference(
                sys_id=f"service:{name.lower()}",
                name=name,
                entity_type="business_service",
                edge_type="affects_ci",
                attributes={"source_kind": "sapphireims_service"},
            )
        )
    return refs


async def _resolve_evidence_for_ticket_id(
    db: AsyncSession, tenant_id: uuid.UUID, ticket_id: str
) -> uuid.UUID | None:
    return (
        await db.execute(
            select(EvidenceItem.id)
            .join(RawEvidenceObject, EvidenceItem.raw_object_ref == RawEvidenceObject.id)
            .join(Source, RawEvidenceObject.source_id == Source.id)
            .where(
                EvidenceItem.tenant_id == tenant_id,
                RawEvidenceObject.tenant_id == tenant_id,
                RawEvidenceObject.external_id == ticket_id,
                Source.source_type == "sapphireims",
            )
            .order_by(EvidenceItem.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def process_sapphireims_references(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    evidence: EvidenceItem,
    payload: dict,
) -> dict:
    """Materialize edges and entities for one SapphireIMS evidence item.
    Idempotent; runs inside the correlation hook's SAVEPOINT. No reverse
    healing: relations are untyped here, and the symmetric case-link
    keys already tie both sides regardless of ingestion order."""
    counts: dict = {"task_edges": 0, "entity_edges": 0, "unresolved_refs": 0}

    for ticket_id in extract_ticket_references(payload):
        target_id = await _resolve_evidence_for_ticket_id(db, tenant_id, ticket_id)
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
            "related_ticket",
            metadata={"origin": "sapphireims_reference"},
            domain_id=evidence.domain_id,
        )
        counts["task_edges"] += 1

    for ref in extract_entity_references(payload):
        entity = await _ensure_entity(db, tenant_id, ref, external_system="sapphireims")
        await ensure_edge(
            db,
            tenant_id,
            "evidence",
            evidence.id,
            "entity",
            entity.id,
            ref.edge_type,
            metadata={"origin": "sapphireims_reference"},
            domain_id=evidence.domain_id,
        )
        counts["entity_edges"] += 1

    return counts
