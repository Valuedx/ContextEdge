"""Load active policy pack and ontology for assessment."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from contextedge.models.playbook_quality import (
    ProductOntologyTerm,
    ProductOntologyVersion,
    QualityPolicyPack,
    QualityPolicyRule,
)
from contextedge.quality.hashing import content_hash
from contextedge.quality.seed_data import load_ontology_payload, load_policy_pack_payload


async def active_policy_rules(
    db: AsyncSession, tenant_id: uuid.UUID
) -> tuple[list[dict[str, Any]], str | None]:
    """Return (rules, pack_version) for the tenant's active policy pack."""
    result = await db.execute(
        select(QualityPolicyPack)
        .where(
            QualityPolicyPack.tenant_id == tenant_id,
            QualityPolicyPack.status == "active",
        )
        .options(selectinload(QualityPolicyPack.rules))
        .order_by(QualityPolicyPack.created_at.desc())
        .limit(1)
    )
    pack = result.scalar_one_or_none()
    if pack is None:
        return [], None
    rules = [
        {
            "id": str(rule.id),
            "normalized_action": rule.normalized_action,
            "decision": rule.decision,
            "alternative_action": rule.alternative_action,
            "applicability": rule.applicability or {},
            "rationale": rule.rationale,
        }
        for rule in pack.rules
    ]
    return rules, pack.version


async def active_ontology_terms(
    db: AsyncSession, tenant_id: uuid.UUID
) -> tuple[list[dict[str, Any]], str | None]:
    """Return (terms, version) for the tenant's active ontology."""
    result = await db.execute(
        select(ProductOntologyVersion)
        .where(
            ProductOntologyVersion.tenant_id == tenant_id,
            ProductOntologyVersion.status == "active",
        )
        .options(selectinload(ProductOntologyVersion.terms))
        .order_by(ProductOntologyVersion.created_at.desc())
        .limit(1)
    )
    ont = result.scalar_one_or_none()
    if ont is None:
        return [], None
    terms = [
        {
            "canonical_term": term.canonical_term,
            "term_kind": term.term_kind,
            "aliases": list(term.aliases or []),
            "parent_term": term.parent_term,
        }
        for term in ont.terms
    ]
    return terms, ont.version


# The ontology term_kind that names the product itself. Everything else in an
# ontology describes parts of a product; exactly one term names the product a
# tenant runs, and that is the only place the system learns it.
PRODUCT_TERM_KIND = "product"


def product_label_from_terms(terms: list[dict[str, Any]]) -> str | None:
    """The tenant's product name, or ``None`` when the ontology does not say.

    ``None`` is a supported answer, not a gap to paper over. A tenant with no
    ontology gets version text with no product name in it — "KB for 8.2.3" —
    which is accurate. Substituting a default here is how "AutomationEdge" ended
    up in the prompt of every tenant in the first place.
    """
    for term in terms or []:
        if term.get("term_kind") == PRODUCT_TERM_KIND:
            label = (term.get("canonical_term") or "").strip()
            if label:
                return label
    return None


async def active_product_label(db: AsyncSession, tenant_id: uuid.UUID) -> str | None:
    """Product name from the tenant's active ontology. Failure-tolerant.

    Never raises: a missing ontology must degrade the prompt's wording, not
    stop generation.
    """
    try:
        terms, _ = await active_ontology_terms(db, tenant_id)
    except Exception:  # noqa: BLE001
        return None
    return product_label_from_terms(terms)


async def seed_policy_pack(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    owner: str | None = None,
    created_by: uuid.UUID | None = None,
    profile: str | None = None,
    payload_path: Path | str | None = None,
    payload: dict[str, Any] | None = None,
) -> QualityPolicyPack:
    """Bootstrap tenant policy pack from JSON (template, profile, path, or dict)."""
    existing = await db.execute(
        select(QualityPolicyPack).where(
            QualityPolicyPack.tenant_id == tenant_id,
            QualityPolicyPack.status == "active",
        )
    )
    found = existing.scalar_one_or_none()
    if found is not None:
        return found

    if payload is None:
        payload = load_policy_pack_payload(profile=profile, path=payload_path)

    pack = QualityPolicyPack(
        tenant_id=tenant_id,
        version=str(payload["version"]),
        status="active",
        owner=owner or payload.get("owner"),
        notes=payload.get("notes"),
        created_by=created_by,
    )
    db.add(pack)
    await db.flush()

    created_rules: list[QualityPolicyRule] = []
    for row in payload.get("rules") or []:
        if not isinstance(row, dict):
            continue
        rule = QualityPolicyRule(
            tenant_id=tenant_id,
            pack_id=pack.id,
            normalized_action=str(row["normalized_action"]),
            decision=str(row["decision"]),
            alternative_action=row.get("alternative_action"),
            rationale=row.get("rationale"),
            source_kind=row.get("source_kind"),
        )
        db.add(rule)
        created_rules.append(rule)
    await db.flush()

    pack.pack_hash = content_hash(
        {
            "version": pack.version,
            "rules": [
                {"action": r.normalized_action, "decision": r.decision}
                for r in created_rules
            ],
        }
    )
    await db.flush()
    from contextedge.services.quality_staleness_hooks import (
        signal_stale_for_tenant_policy_change,
    )

    await signal_stale_for_tenant_policy_change(
        db, tenant_id, origin="policy_pack_seeded"
    )
    return pack


async def seed_ontology(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    owner: str | None = None,
    profile: str | None = None,
    payload_path: Path | str | None = None,
    payload: dict[str, Any] | None = None,
) -> ProductOntologyVersion:
    """Bootstrap tenant ontology from JSON (template, profile, path, or dict)."""
    existing = await db.execute(
        select(ProductOntologyVersion).where(
            ProductOntologyVersion.tenant_id == tenant_id,
            ProductOntologyVersion.status == "active",
        )
    )
    found = existing.scalar_one_or_none()
    if found is not None:
        return found

    if payload is None:
        payload = load_ontology_payload(profile=profile, path=payload_path)

    ont = ProductOntologyVersion(
        tenant_id=tenant_id,
        version=str(payload["version"]),
        status="active",
        owner=owner or payload.get("owner"),
    )
    db.add(ont)
    await db.flush()

    term_rows = []
    for row in payload.get("terms") or []:
        if not isinstance(row, dict):
            continue
        term = ProductOntologyTerm(
            tenant_id=tenant_id,
            ontology_version_id=ont.id,
            canonical_term=str(row["canonical_term"]),
            term_kind=str(row.get("term_kind") or "component"),
            aliases=list(row.get("aliases") or []),
            parent_term=row.get("parent_term"),
        )
        db.add(term)
        term_rows.append(term)
    await db.flush()
    ont.ontology_hash = content_hash(
        [{"term": t.canonical_term, "kind": t.term_kind} for t in term_rows]
    )
    await db.flush()
    from contextedge.services.quality_staleness_hooks import (
        signal_stale_for_tenant_ontology_change,
    )

    await signal_stale_for_tenant_ontology_change(
        db, tenant_id, origin="ontology_seeded"
    )
    return ont


async def seed_default_policy_pack(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    owner: str | None = None,
    created_by: uuid.UUID | None = None,
    profile: str | None = None,
    payload_path: Path | str | None = None,
) -> QualityPolicyPack:
    """Backward-compatible alias — seeds generic template unless profile/path set."""
    return await seed_policy_pack(
        db,
        tenant_id,
        owner=owner,
        created_by=created_by,
        profile=profile,
        payload_path=payload_path,
    )


async def seed_default_ontology(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    owner: str | None = None,
    profile: str | None = None,
    payload_path: Path | str | None = None,
) -> ProductOntologyVersion:
    """Backward-compatible alias — seeds generic template unless profile/path set."""
    return await seed_ontology(
        db,
        tenant_id,
        owner=owner,
        profile=profile,
        payload_path=payload_path,
    )
