"""Layered identity resolution service.

Resolution order (see the identity-resolution design in the 2026-07
review; migration ``0033`` carries the schema):

1. **Strong identifiers** — email / username / hostname / fqdn / ip /
   serial / external system id. Deterministic, confidence 1.0, no LLM.
2. **Typed exact alias** — normalized alias text scoped to the entity
   type, so "Phoenix" the application never matches "Phoenix" the person.
3. **Candidate adjudication** — a small candidate list is scored by the
   LLM, which may abstain (``needs_review``). Auto-link only above a
   per-entity-type threshold.
4. **Provisional creation** — an unmatched mention becomes a
   ``provisional`` identity (not a trusted one), so identity pollution is
   visible and reviewable instead of silent.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Literal

import structlog
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.ai.extractors.identity_extractor import extract_identities
from contextedge.graph.builder import ensure_edge
from contextedge.models.episode import (
    CanonicalIdentity,
    EvidenceIdentityLink,
    IdentityAlias,
)
from contextedge.models.evidence import EvidenceItem
from contextedge.models.pattern import GraphEdge
from contextedge.services.event_log_service import append_operational_event
from contextedge.services.identity_normalizer import (
    NormalizedEntity,
    normalize_extracted_entity,
)

logger = structlog.get_logger()

# Auto-link thresholds for adjudicated (non-deterministic) matches. People
# are held to a stricter bar than infrastructure names.
AUTO_LINK_THRESHOLDS = {"person": 0.95}
DEFAULT_AUTO_LINK_THRESHOLD = 0.9
MAX_ADJUDICATION_CANDIDATES = 5


class AdjudicationResult(BaseModel):
    decision: Literal["match", "new_identity", "needs_review"]
    candidate_id: str | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reason: str = ""


def _normalize_term(value: str) -> str:
    # .lower(), not .casefold(): the 0033 SQL backfill normalizes with
    # PostgreSQL lower(), and both sides must produce identical strings or
    # backfilled aliases become unmatchable (and the strong-alias unique
    # index blocks the correctly-normalized variant).
    return " ".join(value.strip().split()).lower()


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


def _auto_link_threshold(entity_type: str) -> float:
    return AUTO_LINK_THRESHOLDS.get(entity_type, DEFAULT_AUTO_LINK_THRESHOLD)


def _touch_alias(alias: IdentityAlias) -> None:
    alias.times_observed = int(alias.times_observed or 0) + 1
    alias.last_seen_at = datetime.now(UTC)


async def _find_strong_identifier_match(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    entity: NormalizedEntity,
) -> tuple[CanonicalIdentity, IdentityAlias, str] | None:
    for alias_type, value, _source_system in entity.strong_identifiers:
        # No entity_type and no is_active filter here: the lookup must
        # mirror uq_identity_aliases_tenant_strong's full scope (tenant +
        # alias_type + value, nothing else). Filtering on either dimension
        # makes owned identifiers invisible, and Layer 4 then mints endless
        # provisional duplicates whose strong-alias inserts conflict with
        # the still-owned row. A deactivated owner resolving here is
        # reviewable; a duplicate blackhole is not.
        result = await db.execute(
            select(IdentityAlias, CanonicalIdentity)
            .join(
                CanonicalIdentity,
                CanonicalIdentity.id == IdentityAlias.canonical_identity_id,
            )
            .where(
                CanonicalIdentity.tenant_id == tenant_id,
                IdentityAlias.alias_type == alias_type,
                IdentityAlias.normalized_alias == value,
            )
            .limit(1)
        )
        row = result.first()
        if row is not None:
            alias, canonical = row
            return canonical, alias, alias_type
    return None


async def _find_exact_alias_match(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    entity: NormalizedEntity,
) -> tuple[CanonicalIdentity, IdentityAlias] | None:
    result = await db.execute(
        select(IdentityAlias, CanonicalIdentity)
        .join(
            CanonicalIdentity,
            CanonicalIdentity.id == IdentityAlias.canonical_identity_id,
        )
        .where(
            CanonicalIdentity.tenant_id == tenant_id,
            CanonicalIdentity.entity_type == entity.entity_type,
            CanonicalIdentity.is_active.is_(True),
            or_(
                IdentityAlias.normalized_alias == entity.normalized_name,
                # Pre-0033 rows may not have normalized_alias backfilled at
                # ORM level (e.g. mid-deploy); fall back to alias_text.
                (
                    IdentityAlias.normalized_alias.is_(None)
                    & (func.lower(IdentityAlias.alias_text) == entity.normalized_name)
                ),
            ),
        )
        .limit(1)
    )
    row = result.first()
    if row is None:
        return None
    alias, canonical = row
    return canonical, alias


def _candidate_tokens(normalized_name: str) -> list[str]:
    # Strip LIKE metacharacters entirely — evidence-derived text must never
    # inject wildcards into the candidate pattern ("100%" would otherwise
    # match every identity and force a pointless LLM adjudication).
    cleaned = normalized_name.replace("%", " ").replace("_", " ")
    tokens = [t.strip(".,-") for t in cleaned.split()]
    return sorted({t for t in tokens if len(t) >= 3}, key=len, reverse=True)[:3]


async def _candidate_identities(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    entity: NormalizedEntity,
) -> list[CanonicalIdentity]:
    tokens = _candidate_tokens(entity.normalized_name)
    if not tokens:
        return []
    patterns = [f"%{token}%" for token in tokens]
    result = await db.execute(
        select(CanonicalIdentity)
        .where(
            CanonicalIdentity.tenant_id == tenant_id,
            CanonicalIdentity.entity_type == entity.entity_type,
            CanonicalIdentity.is_active.is_(True),
            or_(*[CanonicalIdentity.normalized_name.like(p) for p in patterns]),
        )
        # Deterministic candidate set: without an ORDER BY, which 5 rows
        # survive the LIMIT varies run to run and so do adjudications.
        .order_by(CanonicalIdentity.normalized_name, CanonicalIdentity.id)
        .limit(MAX_ADJUDICATION_CANDIDATES)
    )
    return list(result.scalars().all())


async def _adjudicate_candidates(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    entity: NormalizedEntity,
    candidates: list[CanonicalIdentity],
) -> AdjudicationResult | None:
    """Ask the LLM to pick between candidates or abstain. Fails soft."""
    from contextedge.ai.prompts import get_prompt
    from contextedge.ai.provider import llm_complete_json_validated

    alias_rows = await db.execute(
        select(IdentityAlias).where(
            IdentityAlias.canonical_identity_id.in_([c.id for c in candidates])
        )
    )
    aliases_by_identity: dict[uuid.UUID, list[str]] = {}
    for alias in alias_rows.scalars().all():
        aliases_by_identity.setdefault(alias.canonical_identity_id, []).append(
            alias.alias_text
        )

    incoming = {
        "entity_type": entity.entity_type,
        "name": entity.display_name,
        "identifiers": {
            alias_type: [value for value, _ in bucket]
            for alias_type, bucket in entity.identifiers.items()
        },
        "context": entity.context,
    }
    candidate_payload = [
        {
            "id": str(candidate.id),
            "name": candidate.canonical_name,
            "aliases": aliases_by_identity.get(candidate.id, [])[:10],
            "resolution_state": candidate.resolution_state,
        }
        for candidate in candidates
    ]

    prompt = get_prompt("identity_adjudication", tenant_id)
    try:
        result = await llm_complete_json_validated(
            prompt.format_user(
                incoming=json.dumps(incoming, ensure_ascii=False),
                candidates=json.dumps(candidate_payload, ensure_ascii=False),
            ),
            AdjudicationResult,
            task="classification",
            system_prompt=prompt.system,
            tenant_id=tenant_id,
            db=db,
            prompt_name=prompt.name,
            prompt_version=prompt.version,
        )
    except Exception as exc:
        logger.warning(
            "identity.adjudication_failed",
            tenant_id=str(tenant_id),
            error=str(exc),
        )
        return None
    if isinstance(result, AdjudicationResult):
        return result
    return None


async def _create_identity(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    entity: NormalizedEntity,
    *,
    resolution_state: str,
    confidence: float,
    method: str,
    source_id: uuid.UUID | None,
) -> CanonicalIdentity:
    canonical = CanonicalIdentity(
        tenant_id=tenant_id,
        entity_type=entity.entity_type,
        canonical_name=entity.display_name,
        normalized_name=entity.normalized_name,
        resolution_state=resolution_state,
        resolution_confidence=confidence,
        resolution_method=method,
        metadata_extra={"context": entity.context} if entity.context else None,
    )
    db.add(canonical)
    await db.flush()

    now = datetime.now(UTC)
    db.add(
        IdentityAlias(
            canonical_identity_id=canonical.id,
            tenant_id=tenant_id,
            alias_text=entity.display_name,
            normalized_alias=entity.normalized_name,
            alias_type="display_name",
            source_id=source_id,
            confidence=confidence,
            created_by="system",
            last_seen_at=now,
        )
    )
    await db.flush()

    # Strong-identifier aliases race the tenant-wide unique index
    # (uq_identity_aliases_tenant_strong): two workers extracting the same
    # new email must not abort a whole normalize transaction. Insert with
    # ON CONFLICT DO NOTHING against the partial index; a conflict means a
    # concurrent writer owns the identifier — log and move on (the next
    # mention resolves to the winner via Layer 1).
    for alias_type, value, source_system in entity.strong_identifiers:
        stmt = (
            pg_insert(IdentityAlias)
            .values(
                id=uuid.uuid4(),
                canonical_identity_id=canonical.id,
                tenant_id=tenant_id,
                alias_text=value,
                normalized_alias=value,
                alias_type=alias_type,
                source_system=source_system,
                source_id=source_id,
                confidence=confidence,
                created_by="system",
                last_seen_at=now,
            )
            .on_conflict_do_nothing(
                index_elements=[
                    IdentityAlias.tenant_id,
                    IdentityAlias.alias_type,
                    IdentityAlias.normalized_alias,
                ],
                index_where=text(
                    "alias_type IN ('email', 'username', 'hostname', 'fqdn', "
                    "'ip_address', 'serial_number', 'external_id')"
                ),
            )
            .returning(IdentityAlias.id)
        )
        inserted = (await db.execute(stmt)).scalar_one_or_none()
        if inserted is None:
            logger.warning(
                "identity.strong_alias_conflict",
                tenant_id=str(tenant_id),
                alias_type=alias_type,
                canonical_id=str(canonical.id),
            )
    return canonical


async def _learn_alias(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    canonical: CanonicalIdentity,
    entity: NormalizedEntity,
    *,
    confidence: float,
    created_by: str,
    source_id: uuid.UUID | None,
) -> None:
    """Attach the observed display name to a matched identity so the next
    occurrence resolves deterministically without an LLM call."""
    existing = await db.execute(
        select(IdentityAlias.id).where(
            IdentityAlias.canonical_identity_id == canonical.id,
            or_(
                IdentityAlias.normalized_alias == entity.normalized_name,
                func.lower(IdentityAlias.alias_text) == entity.normalized_name,
            ),
        )
    )
    if existing.first() is not None:
        return
    db.add(
        IdentityAlias(
            canonical_identity_id=canonical.id,
            tenant_id=tenant_id,
            alias_text=entity.display_name,
            normalized_alias=entity.normalized_name,
            alias_type="display_name",
            source_id=source_id,
            confidence=confidence,
            created_by=created_by,
            last_seen_at=datetime.now(UTC),
        )
    )


def _resolved_entry(
    canonical: CanonicalIdentity,
    entity: NormalizedEntity,
    *,
    matched_via: str,
    confidence: float,
) -> dict:
    return {
        "canonical_id": canonical.id,
        "canonical_name": canonical.canonical_name,
        "entity_type": canonical.entity_type,
        "resolution_state": canonical.resolution_state,
        "matched_via": matched_via,
        "alias": entity.display_name,
        "confidence": confidence,
        "context": entity.context,
    }


async def _record_resolution_decision(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    canonical: CanonicalIdentity,
    entity: NormalizedEntity,
    *,
    method: str,
    confidence: float,
    candidate_ids: list[str] | None = None,
    reason: str | None = None,
) -> None:
    await append_operational_event(
        db,
        tenant_id=tenant_id,
        entity_type="canonical_identity",
        entity_id=canonical.id,
        event_type="identity.resolution_decision",
        payload={
            "incoming_name": entity.display_name,
            "entity_type": entity.entity_type,
            "method": method,
            "confidence": confidence,
            "resolution_state": canonical.resolution_state,
            "candidate_ids": candidate_ids or [],
            "reason": reason,
        },
    )


async def resolve_extracted_entities(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    extracted: list[dict],
    source_id: uuid.UUID | None = None,
) -> list[dict]:
    resolved: list[dict] = []

    for raw_entity in extracted:
        entity = normalize_extracted_entity(raw_entity)
        if entity is None:
            continue

        # Layer 1: strong identifiers — deterministic, no LLM.
        strong = await _find_strong_identifier_match(db, tenant_id, entity)
        if strong is not None:
            canonical, alias, alias_type = strong
            _touch_alias(alias)
            await _learn_alias(
                db, tenant_id, canonical, entity,
                confidence=0.95, created_by="strong_match", source_id=source_id,
            )
            method = f"strong:{alias_type}"
            resolved.append(
                _resolved_entry(canonical, entity, matched_via=method, confidence=1.0)
            )
            await _record_resolution_decision(
                db, tenant_id, canonical, entity, method=method, confidence=1.0
            )
            continue

        # Layer 2: typed exact alias.
        exact = await _find_exact_alias_match(db, tenant_id, entity)
        if exact is not None:
            canonical, alias = exact
            _touch_alias(alias)
            resolved.append(
                _resolved_entry(
                    canonical, entity, matched_via="alias_exact", confidence=0.95
                )
            )
            continue

        # Layer 3: candidate generation + LLM adjudication (may abstain).
        candidates = await _candidate_identities(db, tenant_id, entity)
        adjudication = (
            await _adjudicate_candidates(db, tenant_id, entity, candidates)
            if candidates
            else None
        )
        candidate_ids = [str(c.id) for c in candidates]

        if adjudication is not None and adjudication.decision == "match":
            matched = next(
                (c for c in candidates if str(c.id) == adjudication.candidate_id),
                None,
            )
            threshold = _auto_link_threshold(entity.entity_type)
            if matched is not None and adjudication.confidence >= threshold:
                await _learn_alias(
                    db, tenant_id, matched, entity,
                    confidence=adjudication.confidence,
                    created_by="adjudicator",
                    source_id=source_id,
                )
                resolved.append(
                    _resolved_entry(
                        matched,
                        entity,
                        matched_via="llm_adjudicated",
                        confidence=adjudication.confidence,
                    )
                )
                await _record_resolution_decision(
                    db, tenant_id, matched, entity,
                    method="llm_adjudicated",
                    confidence=adjudication.confidence,
                    candidate_ids=candidate_ids,
                    reason=adjudication.reason,
                )
                continue
            # A plausible-but-unproven match must NOT silently link or
            # silently fork — park it for human review.
            canonical = await _create_identity(
                db, tenant_id, entity,
                resolution_state="needs_review",
                confidence=adjudication.confidence,
                method="adjudication_below_threshold",
                source_id=source_id,
            )
            resolved.append(
                _resolved_entry(
                    canonical, entity,
                    matched_via="needs_review",
                    confidence=adjudication.confidence,
                )
            )
            await _record_resolution_decision(
                db, tenant_id, canonical, entity,
                method="adjudication_below_threshold",
                confidence=adjudication.confidence,
                candidate_ids=candidate_ids,
                reason=adjudication.reason,
            )
            continue

        if adjudication is not None and adjudication.decision == "needs_review":
            canonical = await _create_identity(
                db, tenant_id, entity,
                resolution_state="needs_review",
                confidence=adjudication.confidence,
                method="adjudication_abstained",
                source_id=source_id,
            )
            resolved.append(
                _resolved_entry(
                    canonical, entity,
                    matched_via="needs_review",
                    confidence=adjudication.confidence,
                )
            )
            await _record_resolution_decision(
                db, tenant_id, canonical, entity,
                method="adjudication_abstained",
                confidence=adjudication.confidence,
                candidate_ids=candidate_ids,
                reason=adjudication.reason,
            )
            continue

        # Layer 4: provisional creation — never a trusted identity on miss.
        canonical = await _create_identity(
            db, tenant_id, entity,
            resolution_state="provisional",
            confidence=0.5,
            method="unmatched_new",
            source_id=source_id,
        )
        resolved.append(
            _resolved_entry(
                canonical, entity, matched_via="provisional_new", confidence=0.5
            )
        )
        await _record_resolution_decision(
            db, tenant_id, canonical, entity,
            method="unmatched_new",
            confidence=0.5,
            candidate_ids=candidate_ids,
        )

    return resolved


async def resolve_entities_from_text(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    content: str,
    source_id: uuid.UUID | None = None,
) -> list[dict]:
    """Extract entities from text and resolve against canonical identities."""
    extracted = await extract_identities(content, tenant_id=tenant_id, db=db)
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
        existing_refs = evidence.canonical_entity_refs or {}
        existing_refs["identities"] = []
        evidence.canonical_entity_refs = existing_refs
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
                    evidence_item=evidence,
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
                # Downstream consumers (review UI, ranking) need the trust
                # level, not just the confidence float.
                "resolution_state": item.get("resolution_state", "resolved"),
            }
        )

    existing_refs = evidence.canonical_entity_refs or {}
    existing_refs["identities"] = merged_refs
    evidence.canonical_entity_refs = existing_refs
    await db.flush()

    # Edge weight carries the resolution confidence so graph consumers see
    # a provisional mention (0.5) as weaker than a strong-identifier match
    # (1.0) instead of every mention weighing the same. Explicit None check:
    # `or 1.0` would promote a legitimate 0.0 (abstained adjudication) to
    # full trust — the exact inversion this weight exists to prevent.
    for ref in merged_refs:
        confidence = ref.get("confidence")
        await ensure_edge(
            db,
            tenant_id,
            "evidence",
            evidence.id,
            "identity",
            uuid.UUID(ref["canonical_id"]),
            "mentions_identity",
            weight=1.0 if confidence is None else float(confidence),
            metadata={"resolution_state": ref.get("resolution_state", "resolved")},
            domain_id=getattr(evidence, "domain_id", None),
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

    # Only resolved/verified identities feed ranking signals — a provisional
    # identity is an unreviewed guess and must not boost a playbook's score.
    alias_result = await db.execute(
        select(IdentityAlias.canonical_identity_id)
        .join(CanonicalIdentity)
        .where(
            CanonicalIdentity.tenant_id == tenant_id,
            CanonicalIdentity.is_active.is_(True),
            CanonicalIdentity.resolution_state.in_(("resolved", "verified")),
            func.lower(IdentityAlias.alias_text).in_(normalized_terms),
        )
    )
    canonical_result = await db.execute(
        select(CanonicalIdentity.id).where(
            CanonicalIdentity.tenant_id == tenant_id,
            CanonicalIdentity.is_active.is_(True),
            CanonicalIdentity.resolution_state.in_(("resolved", "verified")),
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


async def merge_canonical_identities(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    primary_identity_id: uuid.UUID,
    duplicate_identity_id: uuid.UUID,
    actor_id: uuid.UUID | None = None,
) -> CanonicalIdentity | None:
    if primary_identity_id == duplicate_identity_id:
        return None

    primary = await db.get(CanonicalIdentity, primary_identity_id)
    duplicate = await db.get(CanonicalIdentity, duplicate_identity_id)
    if (
        primary is None
        or duplicate is None
        or primary.tenant_id != tenant_id
        or duplicate.tenant_id != tenant_id
    ):
        return None

    alias_result = await db.execute(
        select(IdentityAlias).where(
            IdentityAlias.canonical_identity_id.in_((primary.id, duplicate.id))
        )
    )
    aliases = list(alias_result.scalars().all())
    # Dedupe key includes alias_type: a duplicate's email/username alias whose
    # text happens to equal a primary display_name must be RE-POINTED, not
    # deleted — deleting it would drop the strong identifier and undo the
    # merge on the next mention.
    existing_aliases: set[tuple[str, str]] = {
        ("display_name", _normalize_term(primary.canonical_name)),
        *(
            (alias.alias_type or "display_name", _normalize_term(alias.alias_text))
            for alias in aliases
            if alias.canonical_identity_id == primary.id
        ),
    }
    for alias in aliases:
        if alias.canonical_identity_id != duplicate.id:
            continue
        key = (alias.alias_type or "display_name", _normalize_term(alias.alias_text))
        if key in existing_aliases:
            await db.delete(alias)
            continue
        alias.canonical_identity_id = primary.id
        existing_aliases.add(key)

    duplicate_normalized = _normalize_term(duplicate.canonical_name)
    if ("display_name", duplicate_normalized) not in existing_aliases:
        db.add(
            IdentityAlias(
                canonical_identity_id=primary.id,
                tenant_id=tenant_id,
                alias_text=duplicate.canonical_name,
                normalized_alias=duplicate_normalized,
                alias_type="display_name",
                source_id=None,
                confidence=1.0,
                created_by="merge",
                last_seen_at=datetime.now(UTC),
            )
        )

    existing_primary_result = await db.execute(
        select(EvidenceIdentityLink.evidence_id).where(
            EvidenceIdentityLink.tenant_id == tenant_id,
            EvidenceIdentityLink.identity_id == primary.id,
        )
    )
    primary_evidence_ids = set(existing_primary_result.scalars().all())
    duplicate_links_result = await db.execute(
        select(EvidenceIdentityLink).where(
            EvidenceIdentityLink.tenant_id == tenant_id,
            EvidenceIdentityLink.identity_id == duplicate.id,
        )
    )
    for link in duplicate_links_result.scalars().all():
        if link.evidence_id in primary_evidence_ids:
            await db.delete(link)
            continue
        link.identity_id = primary.id
        primary_evidence_ids.add(link.evidence_id)

    edges_result = await db.execute(
        select(GraphEdge).where(
            GraphEdge.tenant_id == tenant_id,
            or_(
                (GraphEdge.source_node_type == "identity")
                & (GraphEdge.source_node_id == duplicate.id),
                (GraphEdge.target_node_type == "identity")
                & (GraphEdge.target_node_id == duplicate.id),
            ),
        )
    )
    for edge in edges_result.scalars().all():
        if edge.source_node_type == "identity" and edge.source_node_id == duplicate.id:
            edge.source_node_id = primary.id
        if edge.target_node_type == "identity" and edge.target_node_id == duplicate.id:
            edge.target_node_id = primary.id

    duplicate.is_active = False
    merged_metadata = dict(duplicate.metadata_extra or {})
    merged_metadata["merged_into"] = str(primary.id)
    duplicate.metadata_extra = merged_metadata
    # A human-confirmed merge upgrades the surviving identity.
    primary.resolution_state = "verified"
    primary.resolution_method = "human_merge"

    await db.flush()
    await append_operational_event(
        db,
        tenant_id=tenant_id,
        actor_id=actor_id,
        entity_type="canonical_identity",
        entity_id=primary.id,
        event_type="identity.merged",
        payload={
            "primary_identity_id": str(primary.id),
            "duplicate_identity_id": str(duplicate.id),
        },
    )
    await db.refresh(primary)

    # The normalized link tables were re-pointed above, but the cached JSONB
    # snapshots (evidence.canonical_entity_refs, episode.entity_refs) still
    # reference the duplicate — enqueue the rebuild so they converge.
    # Failure to enqueue must never block the merge itself.
    try:
        from contextedge.workers.identity_tasks import rebuild_identity_snapshots

        rebuild_identity_snapshots.delay(
            str(tenant_id), str(primary.id), str(duplicate.id)
        )
    except Exception as exc:
        logger.warning(
            "identity.snapshot_rebuild_enqueue_failed",
            tenant_id=str(tenant_id),
            primary_identity_id=str(primary.id),
            duplicate_identity_id=str(duplicate.id),
            error=str(exc),
        )
    return primary
