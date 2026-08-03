"""Deterministic structure from Zoho Desk payloads.

The Phase 1 pattern, fourth system. Zoho payloads are already normalized
by the connector, so extraction reads stable keys regardless of portal
configuration.

What becomes an **entity** (with edges):

- ``product_name`` → ``business_service`` with ``affects_ci``. In Zoho
  Desk the product is the thing the ticket is about, which is the same
  role ServiceNow's CI and Jira's component play.
- ``team_name`` → ``assignment_group`` with ``assigned_to_group`` — the
  owning queue, the ServiceNow assignment-group equivalent.
- ``account_name`` → ``customer_account`` with ``affects_ci``. In a
  multi-tenant MSP portal this is *the* grouping that says "these
  incidents hit the same customer".
- KB ``category_name`` → ``knowledge_category`` with ``documents``, so
  an article is reachable from the topic it was filed under.

What becomes a **case-link key** (symmetric, 1.0-confidence
correlation): ``related_tickets``. What deliberately does not: product,
team, account, category. Shared infrastructure must never be a case-link
key — one product name would union every ticket about that product into
a single canonical case. That guard is the same one the ServiceNow
service applies to ``cmdb_ci`` and the Jira service applies to
components; it goes through the entity path instead, where the graph can
express "related to the same thing" without claiming "the same
incident".

Relation *types* are not modeled: Zoho's related/linked ticket lists
carry no relation semantics, so edges stay generic ``related_ticket``
rather than guessed. A wrong ``caused_by_change`` would poison change
risk assessment, and no edge beats a wrong one — the same call the
SapphireIMS service made.

Ticket ids are validated to a conservative shape before they can become
a case-link key: Zoho row ids are 18-digit numbers and ticket numbers
are short alphanumerics, but portals customize the number format, so the
regex is permissive on characters and strict on length and junk.
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

EXTERNAL_SYSTEM = "zoho_desk"

_TICKET_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9\-_/#]{0,39}")
MAX_TICKET_REFS = 20
MAX_TAG_REFS = 25
MAX_NAME_CHARS = 200


def _valid_ticket_id(value: object) -> str | None:
    if isinstance(value, str):
        candidate = value.strip()
        if candidate and _TICKET_ID_RE.fullmatch(candidate):
            return candidate
    if isinstance(value, int) and value > 0:
        return str(value)
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
    """Product / team / account / KB-category anchors.

    External ids are namespaced by kind (``product:``, ``team:``, …) so
    a product named "Support" can never collide with a team of the same
    name. Names are lowercased in the id and preserved in the display
    name, so a rename updates one row rather than forking a duplicate.
    """
    refs: list[EntityReference] = []
    p = payload or {}

    for field, prefix, entity_type, edge_type, source_kind in (
        ("product_name", "product", "business_service", "affects_ci", "zoho_product"),
        ("team_name", "team", "assignment_group", "assigned_to_group", "zoho_team"),
        ("account_name", "account", "customer_account", "affects_ci", "zoho_account"),
        (
            "category_name",
            "kb_category",
            "knowledge_category",
            "documents",
            "zoho_kb_category",
        ),
    ):
        value = p.get(field)
        if not isinstance(value, str) or not value.strip():
            continue
        name = value.strip()[:MAX_NAME_CHARS]
        refs.append(
            EntityReference(
                sys_id=f"{prefix}:{name.lower()}",
                name=name,
                entity_type=entity_type,
                edge_type=edge_type,
                attributes={"source_kind": source_kind},
            )
        )
    return refs


def extract_tag_topics(payload: dict | None) -> list[str]:
    """KB article tags, normalized.

    Tags are the author's own topical index — verified present on live
    articles (``["workflow import on ae server", "workflow import"]``) —
    and they are the cheapest bridge between an article and the incidents
    that share its vocabulary. Emitted as topics, never as case-link keys.
    """
    raw = (payload or {}).get("tags")
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw[:MAX_TAG_REFS]:
        if not isinstance(item, str):
            continue
        tag = " ".join(item.split()).strip()[:MAX_NAME_CHARS]
        if tag and tag.lower() not in {t.lower() for t in out}:
            out.append(tag)
    return out


async def _resolve_evidence_for_ticket_id(
    db: AsyncSession, tenant_id: uuid.UUID, ticket_id: str
) -> uuid.UUID | None:
    """Newest evidence for this Zoho record, scoped to zoho_desk sources
    so another connector's external id can never resolve here (mirror of
    the ServiceNow / Jira source scoping)."""
    return (
        await db.execute(
            select(EvidenceItem.id)
            .join(RawEvidenceObject, EvidenceItem.raw_object_ref == RawEvidenceObject.id)
            .join(Source, RawEvidenceObject.source_id == Source.id)
            .where(
                EvidenceItem.tenant_id == tenant_id,
                RawEvidenceObject.tenant_id == tenant_id,
                RawEvidenceObject.external_id == ticket_id,
                Source.source_type == "zoho_desk",
            )
            .order_by(EvidenceItem.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def process_zoho_desk_references(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    evidence: EvidenceItem,
    payload: dict,
) -> dict:
    """Materialize edges and entities for one Zoho Desk evidence item.

    Idempotent; runs inside the correlation hook's SAVEPOINT. No reverse
    healing: relations are untyped here, and the symmetric case-link keys
    already tie both sides regardless of ingestion order.
    """
    counts: dict = {
        "ticket_edges": 0,
        "entity_edges": 0,
        "topic_edges": 0,
        "unresolved_refs": 0,
    }

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
            metadata={"origin": "zoho_desk_reference"},
            domain_id=evidence.domain_id,
        )
        counts["ticket_edges"] += 1

    for ref in extract_entity_references(payload):
        entity = await _ensure_entity(db, tenant_id, ref, external_system=EXTERNAL_SYSTEM)
        await ensure_edge(
            db,
            tenant_id,
            "evidence",
            evidence.id,
            "entity",
            entity.id,
            ref.edge_type,
            metadata={"origin": "zoho_desk_reference"},
            domain_id=evidence.domain_id,
        )
        counts["entity_edges"] += 1

    for tag in extract_tag_topics(payload):
        entity = await _ensure_entity(
            db,
            tenant_id,
            EntityReference(
                sys_id=f"tag:{tag.lower()}",
                name=tag,
                entity_type="topic",
                edge_type="tagged_with",
                attributes={"source_kind": "zoho_tag"},
            ),
            external_system=EXTERNAL_SYSTEM,
        )
        await ensure_edge(
            db,
            tenant_id,
            "evidence",
            evidence.id,
            "entity",
            entity.id,
            "tagged_with",
            metadata={"origin": "zoho_desk_reference"},
            domain_id=evidence.domain_id,
        )
        counts["topic_edges"] += 1

    return counts
