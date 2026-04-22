"""Decision extraction and graph linking for evidence items.

Extracts operational decisions/actions from evidence text using the AI
decision extractor, resolves actor and target entities against canonical
identities, and persists graph edges so decisions appear in the context graph.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.ai.extractors.decision_extractor import extract_decisions
from contextedge.graph.builder import ensure_edge
from contextedge.models.evidence import EvidenceItem
from contextedge.services.event_log_service import append_operational_event
from contextedge.services.identity_service import resolve_extracted_entities


async def link_evidence_decisions(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    evidence: EvidenceItem,
    content: str,
    source_id: uuid.UUID | None = None,
) -> list[dict]:
    """Extract decisions from evidence text and create graph edges.

    For each extracted decision:
    - Resolves actor and target against canonical identities
    - Creates ``records_decision`` edges (evidence -> actor identity)
    - Creates ``records_action_on`` edges (evidence -> target identity)
    - Stores decision refs on ``evidence.canonical_entity_refs["decisions"]``

    Returns the list of extracted+resolved decision dicts.
    """
    raw_decisions = await extract_decisions(content, tenant_id=tenant_id, db=db)
    if not raw_decisions:
        return []

    resolved_decisions: list[dict] = []

    for dec in raw_decisions:
        actor_name = str(dec.get("actor", "")).strip()
        target_name = str(dec.get("target", "")).strip()
        decision_type = str(dec.get("decision_type", "unknown")).strip()
        action = str(dec.get("action", "")).strip()
        context = dec.get("context")

        edge_metadata = {
            "decision_type": decision_type,
            "action": action,
            "context": context,
        }

        resolved_actor_id: str | None = None
        resolved_target_id: str | None = None

        if actor_name:
            actor_entities = await resolve_extracted_entities(
                db, tenant_id,
                [{"name": actor_name, "entity_type": "person", "context": f"actor: {action}"}],
                source_id=source_id,
            )
            if actor_entities:
                actor_identity_id = uuid.UUID(str(actor_entities[0]["canonical_id"]))
                resolved_actor_id = str(actor_identity_id)
                await ensure_edge(
                    db, tenant_id,
                    "evidence", evidence.id,
                    "identity", actor_identity_id,
                    "records_decision",
                    metadata=edge_metadata,
                    domain_id=getattr(evidence, "domain_id", None),
                )

        if target_name:
            target_entities = await resolve_extracted_entities(
                db, tenant_id,
                [{"name": target_name, "entity_type": "service", "context": f"target: {action}"}],
                source_id=source_id,
            )
            if target_entities:
                target_identity_id = uuid.UUID(str(target_entities[0]["canonical_id"]))
                resolved_target_id = str(target_identity_id)
                await ensure_edge(
                    db, tenant_id,
                    "evidence", evidence.id,
                    "identity", target_identity_id,
                    "records_action_on",
                    metadata=edge_metadata,
                    domain_id=getattr(evidence, "domain_id", None),
                )

        resolved_decisions.append({
            "decision_type": decision_type,
            "action": action,
            "actor": actor_name,
            "actor_identity_id": resolved_actor_id,
            "target": target_name,
            "target_identity_id": resolved_target_id,
            "context": context,
        })

    existing_refs = evidence.canonical_entity_refs or {}
    existing_refs["decisions"] = resolved_decisions
    evidence.canonical_entity_refs = existing_refs
    await db.flush()

    await append_operational_event(
        db,
        tenant_id=tenant_id,
        entity_type="evidence_item",
        entity_id=evidence.id,
        event_type="decision.extracted",
        payload={
            "decision_count": len(resolved_decisions),
            "decisions": resolved_decisions,
        },
    )

    return resolved_decisions
