"""Claim population from the relevance pass (roadmap A4).

The ``claims`` table, ``claim`` node type, and ``asserted_in`` edge have
been in the schema and the maf.v1 profile since they shipped — with
zero writers. This service is the populator: 0-3 atomic assertions per
relevant evidence item, emitted by the same relevance call that already
reads every body (no extra LLM call), deduplicated tenant-wide on
normalized text.

Lifecycle by default: new claims land ``unverified``, which the
projection's visibility gate excludes — the graph accumulates candidate
assertions without the agent seeing an unreviewed one. Validation
(machine or human) is what promotes a claim into the projection.
A re-asserted claim (same normalized text from new evidence) gains an
additional ``asserted_in`` edge instead of a duplicate row — corroboration
accrues on one claim, not across copies.
"""

from __future__ import annotations

import re
import uuid

import structlog
from sqlalchemy import func, select

from contextedge.models.claim import Claim

logger = structlog.get_logger()

# The relevance-classifier claim vocabulary maps onto the model's
# CLAIM_TYPES directly (both use these names).
MAX_CLAIMS_PER_EVIDENCE = 3
_WS_RE = re.compile(r"\s+")


def normalize_claim_text(text: str) -> str:
    return _WS_RE.sub(" ", (text or "").strip().lower())


async def persist_extracted_claims(
    db,
    tenant_id: uuid.UUID,
    evidence,
    claims: list[dict],
) -> dict:
    """Persist parsed claims for one evidence item; link via asserted_in."""
    counts = {"created": 0, "linked": 0}
    if not claims:
        return counts

    from contextedge.graph.builder import ensure_edge

    for item in claims[:MAX_CLAIMS_PER_EVIDENCE]:
        text = (item.get("text") or "").strip()
        if not text:
            continue
        normalized = normalize_claim_text(text)
        existing = (
            await db.execute(
                select(Claim)
                .where(
                    Claim.tenant_id == tenant_id,
                    func.lower(Claim.claim_text) == normalized,
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if existing is None:
            claim = Claim(
                tenant_id=tenant_id,
                domain_id=getattr(evidence, "domain_id", None),
                claim_type=item.get("type", "symptom"),
                claim_text=text,
                confidence=float(item.get("confidence", 0.5)),
                created_by="relevance_v3",
                created_by_type="agent",
                validation_status="unverified",
            )
            db.add(claim)
            await db.flush()
            counts["created"] += 1
        else:
            claim = existing

        await ensure_edge(
            db,
            tenant_id,
            source_type="claim",
            source_id=claim.id,
            target_type="evidence",
            target_id=evidence.id,
            edge_type="asserted_in",
            weight=1.0,
            confidence=float(item.get("confidence", 0.5)),
            domain_id=getattr(evidence, "domain_id", None),
        )
        counts["linked"] += 1
    return counts
