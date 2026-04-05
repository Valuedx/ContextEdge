"""Identity resolution service for canonicalizing entities across sources."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.ai.extractors.identity_extractor import extract_identities
from contextedge.models.episode import CanonicalIdentity, IdentityAlias


async def resolve_entities_from_text(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    content: str,
    source_id: uuid.UUID | None = None,
) -> list[dict]:
    """Extract entities from text and resolve against canonical identities."""
    extracted = await extract_identities(content)
    resolved = []

    for entity in extracted:
        name = entity.get("name", "").strip()
        entity_type = entity.get("entity_type", "unknown")
        if not name:
            continue

        existing_alias = await db.execute(
            select(IdentityAlias)
            .join(CanonicalIdentity)
            .where(
                CanonicalIdentity.tenant_id == tenant_id,
                IdentityAlias.alias_text == name,
            )
        )
        alias = existing_alias.scalar_one_or_none()

        if alias:
            canonical = await db.get(CanonicalIdentity, alias.canonical_identity_id)
            resolved.append({
                "canonical_id": str(canonical.id),
                "canonical_name": canonical.canonical_name,
                "entity_type": canonical.entity_type,
                "matched_via": "alias",
                "alias": name,
            })
        else:
            canonical = CanonicalIdentity(
                tenant_id=tenant_id,
                entity_type=entity_type,
                canonical_name=name,
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

            resolved.append({
                "canonical_id": str(canonical.id),
                "canonical_name": name,
                "entity_type": entity_type,
                "matched_via": "new",
                "alias": name,
            })

    return resolved
