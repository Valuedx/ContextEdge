"""Identity resolution service for canonicalizing entities across sources."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.ai.extractors.identity_extractor import extract_identities
from contextedge.graph.builder import link_node_to_identities
from contextedge.models.episode import CanonicalIdentity, EvidenceIdentityLink, IdentityAlias
from contextedge.models.evidence import EvidenceItem
from contextedge.services.event_log_service import append_operational_event


def _normalize_term(value: str) -> str:
    return " ".join(value.strip().split()).casefold()


def identity_ids_from_refs(entity_refs: dict | None) -> list[uuid.UUID]:
    if not entity_refs:
        return []
    values = entity_refs.get("identities")
    if not isinstance(values, list):
        return []
    found: list[uuid.UUID] = []
    seen: set[uuid.UUID] = set()
    for item in values:
        if not isinstance(item, dict) or not item.get("canonical_id"):
            continue
        try:
            identity_id = uuid.UUID(str(item["canonical_id"]))
        except (TypeError, ValueError):
            continue
        if identity_id in seen:
            continue
        seen.add(identity_id)
        found.append(identity_id)
    return found


async def resolve_extracted_entities(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    extracted: list[dict],
    source_id: uuid.UUID | None = None,
) -> list[dict]:
    resolved: list[dict] = []

    for entity in extracted:
        name = str(entity.get("name", "")).strip()
        entity_type = str(entity.get("entity_type", "unknown")).strip() or "unknown"
        context = entity.get("context")
        if not name:
            continue

        normalized = _normalize_term(name)
        existing_alias = await db.execute(
            select(IdentityAlias)
            .join(CanonicalIdentity)
            .where(
                CanonicalIdentity.tenant_id == tenant_id,
                func.lower(IdentityAlias.alias_text) == normalized,
            )
        )
        alias = existing_alias.scalar_one_or_none()

        if alias:
            canonical = await db.get(CanonicalIdentity, alias.canonical_identity_id)
            if canonical is None:
                continue
            resolved.append(
                {
                    "canonical_id": canonical.id,
                    "canonical_name": canonical.canonical_name,
                    "entity_type": canonical.entity_type,
                    "matched_via": "alias",
                    "alias": name,
                    "confidence": float(alias.confidence or 1.0),
                    "context": context,
                }
            )
            continue

        canonical = CanonicalIdentity(
            tenant_id=tenant_id,
            entity_type=entity_type,
            canonical_name=name,
            metadata_extra={"context": context} if context else None,
        )
        db.add(canonical)
        await db.flush()

        alias_record = IdentityAlias(
            canonical_identity_id=canonical.id,
            alias_text=name,
            source_id=source_id,
            confidence=0.8,
            created_by="system",
        )
        db.add(alias_record)
        await db.flush()

        resolved.append(
            {
                "canonical_id": canonical.id,
                "canonical_name": name,
                "entity_type": entity_type,
                "matched_via": "new",
                "alias": name,
                "confidence": 0.8,
                "context": context,
            }
        )

    return resolved


async def resolve_entities_from_text(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    content: str,
    source_id: uuid.UUID | None = None,
) -> list[dict]:
    """Extract entities from text and resolve against canonical identities."""
    extracted = await extract_identities(content)
    return await resolve_extracted_entities(db, tenant_id, extracted, source_id=source_id)


async def link_evidence_identities(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    evidence: EvidenceItem,
    content: str,
    source_id: uuid.UUID | None = None,
    source_metadata: dict | None = None,
) -> list[dict]:
    resolved = await resolve_entities_from_text(db, tenant_id, content, source_id=source_id)
    if not resolved:
        evidence.canonical_entity_refs = {"identities": []}
        await db.flush()
        return []

    existing_result = await db.execute(
        select(EvidenceIdentityLink).where(
            EvidenceIdentityLink.tenant_id == tenant_id,
            EvidenceIdentityLink.evidence_id == evidence.id,
        )
    )
    existing_links = {
        link.identity_id: link
        for link in existing_result.scalars().all()
    }

    merged_refs: list[dict] = []
    linked_identity_ids: list[uuid.UUID] = []
    seen: set[uuid.UUID] = set()
    for item in resolved:
        identity_id = uuid.UUID(str(item["canonical_id"]))
        if identity_id in seen:
            continue
        seen.add(identity_id)
        linked_identity_ids.append(identity_id)
        if identity_id not in existing_links:
            db.add(
                EvidenceIdentityLink(
                    tenant_id=tenant_id,
                    evidence_id=evidence.id,
                    identity_id=identity_id,
                    match_type=str(item.get("matched_via") or "alias"),
                    confidence=float(item.get("confidence") or 0.8),
                    source_metadata=source_metadata,
                )
            )
        merged_refs.append(
            {
                "canonical_id": str(identity_id),
                "canonical_name": item["canonical_name"],
                "entity_type": item["entity_type"],
                "alias": item.get("alias"),
                "matched_via": item.get("matched_via"),
                "confidence": float(item.get("confidence") or 0.0),
            }
        )

    evidence.canonical_entity_refs = {"identities": merged_refs}
    await db.flush()

    await link_node_to_identities(
        db,
        tenant_id,
        "evidence",
        evidence.id,
        linked_identity_ids,
        edge_type="mentions_identity",
    )
    await append_operational_event(
        db,
        tenant_id=tenant_id,
        entity_type="evidence_item",
        entity_id=evidence.id,
        event_type="identity.resolved",
        payload={
            "identity_count": len(linked_identity_ids),
            "identities": merged_refs,
        },
    )
    return merged_refs


async def resolve_identity_ids_for_terms(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    terms: list[str],
) -> set[uuid.UUID]:
    normalized_terms = [_normalize_term(term) for term in terms if term and term.strip()]
    if not normalized_terms:
        return set()

    alias_result = await db.execute(
        select(IdentityAlias.canonical_identity_id)
        .join(CanonicalIdentity)
        .where(
            CanonicalIdentity.tenant_id == tenant_id,
            func.lower(IdentityAlias.alias_text).in_(normalized_terms),
        )
    )
    canonical_result = await db.execute(
        select(CanonicalIdentity.id).where(
            CanonicalIdentity.tenant_id == tenant_id,
            func.lower(CanonicalIdentity.canonical_name).in_(normalized_terms),
        )
    )
    return set(alias_result.scalars().all()) | set(canonical_result.scalars().all())


async def get_identity_ids_for_evidence(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    evidence_id: uuid.UUID,
) -> set[uuid.UUID]:
    result = await db.execute(
        select(EvidenceIdentityLink.identity_id).where(
            EvidenceIdentityLink.tenant_id == tenant_id,
            EvidenceIdentityLink.evidence_id == evidence_id,
        )
    )
    return set(result.scalars().all())


async def find_related_evidence_ids_by_identity_ids(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    identity_ids: set[uuid.UUID],
    *,
    exclude_evidence_id: uuid.UUID | None = None,
) -> set[uuid.UUID]:
    if not identity_ids:
        return set()
    stmt = select(EvidenceIdentityLink.evidence_id).where(
        EvidenceIdentityLink.tenant_id == tenant_id,
        EvidenceIdentityLink.identity_id.in_(tuple(identity_ids)),
    )
    if exclude_evidence_id is not None:
        stmt = stmt.where(EvidenceIdentityLink.evidence_id != exclude_evidence_id)
    result = await db.execute(stmt)
    return set(result.scalars().all())
