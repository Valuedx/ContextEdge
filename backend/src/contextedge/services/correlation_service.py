"""Case correlation service for linking evidence across sources."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.models.episode import CorrelationEdge


async def create_correlation(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    source_evidence_id: uuid.UUID,
    target_evidence_id: uuid.UUID,
    correlation_type: str,
    confidence: float,
    explanation: str | None = None,
    created_by: str = "system",
) -> CorrelationEdge:
    """Create a correlation edge between two evidence items."""
    edge = CorrelationEdge(
        tenant_id=tenant_id,
        source_evidence_id=source_evidence_id,
        target_evidence_id=target_evidence_id,
        correlation_type=correlation_type,
        confidence=confidence,
        explanation=explanation,
        created_by=created_by,
    )
    db.add(edge)
    await db.flush()
    return edge


async def get_correlated_evidence(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    evidence_id: uuid.UUID,
) -> list[CorrelationEdge]:
    """Get all evidence correlated to a given evidence item."""
    result = await db.execute(
        select(CorrelationEdge).where(
            CorrelationEdge.tenant_id == tenant_id,
            (CorrelationEdge.source_evidence_id == evidence_id)
            | (CorrelationEdge.target_evidence_id == evidence_id),
        )
    )
    return list(result.scalars().all())
