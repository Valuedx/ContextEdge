"""Review workflow for agent-proposed dependencies.

``propose_dependency`` writes a ``proposed_depends_on`` edge — excluded
from the maf.v1 allowlist so no agent ever consumes unreviewed topology.
Until this service existed there was no approve/reject path, so
proposals accumulated invisibly: recorded but ungoverned. The workflow:

- approve → an authored ``depends_on`` edge is ensured with full
  provenance (which proposal, whose review, the agent's rationale and
  evidence), and the proposal edge is CLOSED (``valid_to``), never
  deleted — the proposal trail is audit history.
- reject → the proposal edge is closed with the review verdict in its
  metadata. Nothing else is written.

Idempotence: approving a dependency that already exists authored simply
closes the proposal against the existing edge (ensure_edge returns it).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import structlog
from sqlalchemy import select

from contextedge.models.entity import Entity
from contextedge.models.pattern import GraphEdge

logger = structlog.get_logger()

PROPOSAL_EDGE_TYPE = "proposed_depends_on"
# A human accepted an agent's discovery: stronger than the raw proposal
# (0.3) but below CMDB-authored certainty — the review validated the
# claim, not the whole topology.
APPROVED_CONFIDENCE = 0.7


async def _active_proposal(
    db,
    tenant_id: uuid.UUID,
    edge_id: uuid.UUID,
    allowed_domain_ids: list[uuid.UUID] | None = None,
):
    conditions = [
        GraphEdge.id == edge_id,
        GraphEdge.tenant_id == tenant_id,
        GraphEdge.edge_type == PROPOSAL_EDGE_TYPE,
        GraphEdge.valid_to.is_(None),
    ]
    if allowed_domain_ids is not None:
        # Fail closed for domain-limited identities: reviewing a
        # proposal WRITES topology, so domainless proposals need
        # tenant-wide authority.
        conditions.append(GraphEdge.domain_id.in_(allowed_domain_ids))
    return (
        await db.execute(select(GraphEdge).where(*conditions))
    ).scalar_one_or_none()


async def list_edge_proposals(
    db,
    tenant_id: uuid.UUID,
    *,
    limit: int = 100,
    allowed_domain_ids: list[uuid.UUID] | None = None,
) -> list[dict]:
    """Pending proposals with resolved CI names for the reviewer."""
    conditions = [
        GraphEdge.tenant_id == tenant_id,
        GraphEdge.edge_type == PROPOSAL_EDGE_TYPE,
        GraphEdge.valid_to.is_(None),
    ]
    if allowed_domain_ids is not None:
        conditions.append(GraphEdge.domain_id.in_(allowed_domain_ids))
    edges = (
        (
            await db.execute(
                select(GraphEdge)
                .where(*conditions)
                .order_by(GraphEdge.created_at.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    entity_ids = {e.source_node_id for e in edges} | {e.target_node_id for e in edges}
    names: dict[uuid.UUID, str] = {}
    if entity_ids:
        rows = (
            await db.execute(
                select(Entity.id, Entity.name).where(
                    Entity.tenant_id == tenant_id, Entity.id.in_(entity_ids)
                )
            )
        ).all()
        names = dict(rows)
    return [
        {
            "edge_id": str(e.id),
            "source_ci": names.get(e.source_node_id, str(e.source_node_id)),
            "target_ci": names.get(e.target_node_id, str(e.target_node_id)),
            "rationale": (e.metadata_extra or {}).get("rationale", ""),
            "evidence_ids": (e.metadata_extra or {}).get("evidence_ids", []),
            "proposed_at": e.created_at.isoformat() if e.created_at else None,
        }
        for e in edges
    ]


def _close(edge, *, decision: str, reviewed_by: str, note: str | None) -> None:
    edge.valid_to = datetime.now(UTC)
    edge.metadata_extra = {
        **(edge.metadata_extra or {}),
        "review": {
            "decision": decision,
            "by": reviewed_by,
            "at": datetime.now(UTC).isoformat(),
            "note": (note or None),
        },
    }


async def approve_edge_proposal(
    db,
    tenant_id: uuid.UUID,
    edge_id: uuid.UUID,
    *,
    reviewed_by: str,
    note: str | None = None,
    allowed_domain_ids: list[uuid.UUID] | None = None,
) -> dict:
    from contextedge.graph.builder import ensure_edge

    proposal = await _active_proposal(db, tenant_id, edge_id, allowed_domain_ids)
    if proposal is None:
        return {"error": "proposal_not_found"}

    meta = proposal.metadata_extra or {}
    authored = await ensure_edge(
        db,
        tenant_id,
        source_type="entity",
        source_id=proposal.source_node_id,
        target_type="entity",
        target_id=proposal.target_node_id,
        edge_type="depends_on",
        weight=1.0,
        confidence=APPROVED_CONFIDENCE,
        metadata={
            "origin": "agent_proposal_approved",
            "proposal_edge_id": str(proposal.id),
            "rationale": meta.get("rationale", ""),
            "evidence_ids": meta.get("evidence_ids", []),
            "reviewed_by": reviewed_by,
        },
        domain_id=proposal.domain_id,
    )
    _close(proposal, decision="approved", reviewed_by=reviewed_by, note=note)
    await db.flush()
    logger.info(
        "edge_proposal.approved",
        tenant_id=str(tenant_id),
        proposal_id=str(proposal.id),
        authored_edge_id=str(authored.id),
    )
    return {"status": "approved", "authored_edge_id": str(authored.id)}


async def reject_edge_proposal(
    db,
    tenant_id: uuid.UUID,
    edge_id: uuid.UUID,
    *,
    reviewed_by: str,
    note: str | None = None,
    allowed_domain_ids: list[uuid.UUID] | None = None,
) -> dict:
    proposal = await _active_proposal(db, tenant_id, edge_id, allowed_domain_ids)
    if proposal is None:
        return {"error": "proposal_not_found"}
    _close(proposal, decision="rejected", reviewed_by=reviewed_by, note=note)
    await db.flush()
    logger.info(
        "edge_proposal.rejected",
        tenant_id=str(tenant_id),
        proposal_id=str(proposal.id),
    )
    return {"status": "rejected"}
