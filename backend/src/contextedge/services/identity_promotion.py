"""Letting the corpus, rather than a reviewer, earn an identity its trust.

Correlation's identity tier only trusts `resolved`/`verified` identities. But
every first-sighting identity is created `provisional`, and the only promotion
path was a human in the review queue. On a fresh tenant the consequence is
total and silent: no identity is ever trusted, so no correlation edge is ever
created, so `reconstruct_episode` is never triggered, so there are no
episodes, no patterns and no playbooks. Measured on the live Zoho backfill —
957 active identities, **zero** resolved, 493 evidence items, zero episodes —
while every other number on the dashboard looked healthy.

A cold start therefore produced a pile of evidence and called it a graph.

**Repetition is the signal.** An identity extracted independently from two
separate documents has been corroborated by the corpus itself, which is
better evidence than a reviewer approving a list of 900 names they have never
seen. And the threshold is not arbitrary: correlation needs two evidence
items sharing an identity, so a degree-1 identity cannot produce an edge at
all. The point where an identity becomes *useful* is exactly the point where
it becomes *corroborated*.

**Rarity is the guard.** Promotion stops at `RARE_DEGREE_MAX`. In a
single-product support corpus the product's own components appear in nearly
every ticket: measured here, 34 identities in the common band (`Process
Studio`, `AE server`) would have generated 9,036 correlation edges against
277 from the 105 rare ones. Those edges glue unrelated incidents together —
the mass-merge trap `correlation_service` already warns about. A shared rare
entity means "same incident"; a shared product name means "same product".

Two things are deliberately left alone:

- **`needs_review` identities.** Those came from `adjudication_abstained` and
  `adjudication_below_threshold`, where the model could not tell whether the
  identity duplicates an existing one. That is real ambiguity, a human adds
  real information, and a wrong merge there is the expensive mistake.
- **Identities that later grow past the rare band.** They keep the trust they
  earned; no demotion. `correlation_service` already re-reads degree on every
  call, so a promoted identity that becomes common is scored down to
  `COMMON_ENTITY_CONFIDENCE` and one that becomes a hub is dropped entirely.
  Re-litigating that here would duplicate a decision that is already made,
  correctly, at the point of use.
"""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.models.episode import CanonicalIdentity, EvidenceIdentityLink

logger = structlog.get_logger()

# An identity linked to fewer evidence items than this cannot correlate
# anything: correlation is a statement about two documents.
CORROBORATION_DEGREE_MIN = 2

# Mirrors `correlation_service.RARE_DEGREE_MAX`. Kept as its own constant
# because importing that module here would close an import cycle
# (correlation_service imports identity_service, which imports this).
# `test_identity_promotion.py` asserts the two stay equal — if they drift,
# this promotes identities the correlation tier no longer considers rare.
RARE_DEGREE_MAX = 5

PROMOTABLE_STATE = "provisional"
PROMOTED_STATE = "resolved"
PROMOTED_METHOD = "corroborated"


async def promote_corroborated_identities(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    identity_ids: list[uuid.UUID],
) -> list[uuid.UUID]:
    """Promote any of these identities that have just earned it.

    Call after the evidence-identity links are flushed, so the degree count
    includes the link that may have crossed the threshold.

    Never raises: promotion is an enhancement to correlation, and failing to
    promote costs a graph edge, while failing the whole normalization would
    cost the evidence. Returns the ids actually promoted.
    """
    if not identity_ids:
        return []

    try:
        degree = (
            select(
                EvidenceIdentityLink.identity_id,
                func.count(func.distinct(EvidenceIdentityLink.evidence_id)).label("deg"),
            )
            .where(
                EvidenceIdentityLink.tenant_id == tenant_id,
                EvidenceIdentityLink.identity_id.in_(tuple(identity_ids)),
            )
            .group_by(EvidenceIdentityLink.identity_id)
            .subquery()
        )

        rows = (
            await db.execute(
                select(CanonicalIdentity)
                .join(degree, degree.c.identity_id == CanonicalIdentity.id)
                .where(
                    CanonicalIdentity.tenant_id == tenant_id,
                    CanonicalIdentity.is_active.is_(True),
                    CanonicalIdentity.resolution_state == PROMOTABLE_STATE,
                    degree.c.deg >= CORROBORATION_DEGREE_MIN,
                    degree.c.deg <= RARE_DEGREE_MAX,
                )
            )
        ).scalars().all()

        promoted: list[uuid.UUID] = []
        for identity in rows:
            identity.resolution_state = PROMOTED_STATE
            identity.resolution_method = PROMOTED_METHOD
            promoted.append(identity.id)

        if promoted:
            await db.flush()
            logger.info(
                "identity.promoted_by_corroboration",
                tenant_id=str(tenant_id),
                count=len(promoted),
                names=[i.canonical_name for i in rows][:10],
            )
        return promoted
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "identity.promotion_failed",
            tenant_id=str(tenant_id),
            error_type=type(exc).__name__,
        )
        return []
