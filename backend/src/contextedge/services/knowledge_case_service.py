"""Attaching documented knowledge to patterns, and reading the ledger back.

Knowledge cases do not cluster the way episodes do. Two incidents are
similar because they happened similarly; two articles are similar because
someone wrote them similarly, and 600 articles behaving like 600 incidents
is the failure this whole split exists to avoid. So a case does not seek
other cases — it attaches to the pattern it documents, or, when no pattern
covers it yet, seeds one.

That second branch is the cold start. A pattern can exist before any
incident does, supported only by documentation, and *graduate* as real
incidents arrive:

    KB-441 -> KC-441 -> P-42     documented_support=1, empirical=0
    EP-912 (success)  -> P-42    documented_support=1, empirical_success=1

The pattern graduates. KC-441 does not — it stays permanently
"documentation said this". That is why nothing here writes an empirical
number onto a knowledge case: how well a documented resolution actually
works is measured from episodes and lives on the ledger, and the database
refuses to record it any other way.

The same ledger makes the reverse question answerable. When a documented
resolution accumulates `contradicts_resolution` rows from recent episodes
while its article stays approved upstream, the KB is stale — and nothing
else in the system would have noticed.
"""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.models.knowledge_case import KnowledgeCase
from contextedge.models.pattern import Pattern, PatternEvidence

logger = structlog.get_logger()

# Distance from a case to a pattern's nearest member episode, on the same
# scale and calibrated the same way as the clustering thresholds: random
# approved-episode pairs on this corpus sit at p01 0.257, median 0.409.
# Deliberately tighter than clustering's own match prefilter (0.30) because
# a wrong attachment here is worse than a missed one — it puts a document
# behind a procedure it does not actually describe, and the playbook
# generator will cite it.
KNOWLEDGE_ATTACH_MAX_DISTANCE = 0.27

# Confidence for a pattern seeded from documentation alone. Below the 0.5
# playbook-generation floor on purpose: a documented-only pattern is a
# candidate, not something to write a procedure from until an incident
# confirms it. It rises when empirical evidence arrives.
DOCUMENTED_ONLY_PATTERN_CONFIDENCE = 0.4


async def _nearest_pattern(
    db: AsyncSession, tenant_id: uuid.UUID, case: KnowledgeCase
) -> tuple[uuid.UUID, float] | None:
    """The pattern owning the member episode closest to this case.

    Patterns carry no embedding of their own, so proximity is measured to
    their members — the same thing clustering does, and ORDERED, which is
    the bug clustering had: `LIMIT 1` unordered returns an arbitrary
    qualifying pattern, and on a corpus this dense that is very nearly a
    random one.

    The domain filter uses CAST rather than Postgres's double-colon cast
    operator. Inside a text() construct that operator collides with the
    bound-parameter prefix, the placeholder is passed through literally,
    and Postgres rejects it at execution time — invisible to import,
    to linting, and to any test that does not actually run the query.
    Note also that this applies inside SQL COMMENTS: a comment here
    explaining the hazard created a phantom bind parameter and broke the
    statement a second time, which is why the explanation lives up here in
    Python instead.
    """
    if case.embedding is None:
        return None
    row = (
        await db.execute(
            text(
                """
                SELECT p.id,
                       min(e.embedding <=> (
                           SELECT embedding FROM knowledge_cases WHERE id = :case_id
                       )) AS distance
                FROM pattern_evidence_links l
                JOIN patterns p ON p.id = l.pattern_id
                JOIN episodes e ON e.id = l.episode_id
                WHERE p.tenant_id = :tenant_id
                  AND p.active_flag IS TRUE
                  AND e.embedding IS NOT NULL
                  AND (
                      CAST(:domain_id AS uuid) IS NULL
                      OR p.domain_id = CAST(:domain_id AS uuid)
                  )
                GROUP BY p.id
                ORDER BY distance ASC
                LIMIT 1
                """
            ),
            {
                "case_id": str(case.id),
                "tenant_id": str(tenant_id),
                "domain_id": str(case.domain_id) if case.domain_id else None,
            },
        )
    ).first()
    if row is None or row[1] is None:
        return None
    return row[0], float(row[1])


async def _record(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    pattern_id: uuid.UUID,
    case: KnowledgeCase,
    *,
    confidence: float,
    support_role: str = "supports_resolution",
) -> None:
    """One ledger row. `documented`, never `empirical` — the database
    enforces that too, so this is belt and braces rather than the only
    guard."""
    db.add(
        PatternEvidence(
            tenant_id=tenant_id,
            pattern_id=pattern_id,
            evidence_object_type="knowledge_case",
            evidence_object_id=case.id,
            support_role=support_role,
            evidence_class="documented",
            strength=1.0,
            confidence=confidence,
            observed_at=None,  # a document did not occur
            outcome=None,
        )
    )


async def attach_case(
    db: AsyncSession, tenant_id: uuid.UUID, case: KnowledgeCase, *, validate: bool = True
) -> dict:
    """Attach one knowledge case to the pattern it documents, or seed one.

    Never raises: a case that cannot be placed is reported and left alone,
    because failing here would block the ingest path that creates it.
    """
    try:
        nearest = await _nearest_pattern(db, tenant_id, case)
    except Exception as exc:  # noqa: BLE001
        logger.warning("knowledge_case.match_failed", case_id=str(case.id), error=str(exc))
        return {"status": "error", "case_id": str(case.id)}

    if nearest is not None and nearest[1] <= KNOWLEDGE_ATTACH_MAX_DISTANCE:
        pattern_id, distance = nearest
        pattern = await db.get(Pattern, pattern_id)
        accepted = True
        reason = None
        if validate and pattern is not None:
            # Same adjudication clustering uses. Distance says "these are
            # about the same subject"; it cannot say "this document
            # describes this pattern's problem", and attaching a document
            # to the wrong procedure is a citation the generator will
            # repeat.
            from contextedge.ai.extractors.pattern_extractor import (
                validate_pattern_match,
            )

            try:
                verdict = await validate_pattern_match(
                    {
                        "title": case.title,
                        "root_cause_summary": case.documented_cause,
                        "final_outcome": case.documented_resolution,
                    },
                    {
                        "title": pattern.title,
                        "description": pattern.description,
                        "root_causes": pattern.root_causes,
                        "resolution_steps": pattern.resolution_steps,
                    },
                    tenant_id=tenant_id,
                    db=db,
                )
                accepted = bool(verdict.get("is_match"))
                reason = verdict.get("reason")
            except Exception as exc:  # noqa: BLE001 — fall back to distance
                logger.warning(
                    "knowledge_case.validate_failed",
                    case_id=str(case.id),
                    error=str(exc),
                )
        if accepted:
            await _record(db, tenant_id, pattern_id, case, confidence=1.0 - distance)
            logger.info(
                "knowledge_case.attached",
                case_id=str(case.id),
                pattern_id=str(pattern_id),
                distance=round(distance, 3),
            )
            return {
                "status": "attached",
                "pattern_id": str(pattern_id),
                "distance": round(distance, 3),
            }
        logger.info(
            "knowledge_case.match_rejected", case_id=str(case.id), reason=reason
        )

    # Nothing covers this document yet. Seeding is the cold-start half:
    # without it a documented failure mode stays invisible until somebody
    # hits it, which is precisely when the documentation would have helped.
    pattern = Pattern(
        tenant_id=tenant_id,
        domain_id=case.domain_id,
        title=case.title[:500],
        description=case.symptom_summary or case.documented_cause,
        pattern_type="recurring_issue",
        confidence=DOCUMENTED_ONLY_PATTERN_CONFIDENCE,
        episode_count=0,  # nothing has happened; this is not false modesty
        root_causes=[case.documented_cause] if case.documented_cause else None,
        resolution_steps=(
            [case.documented_resolution] if case.documented_resolution else None
        ),
        generation_provenance={
            "seeded_from_knowledge_case": str(case.id),
            "source_evidence_id": str(case.source_evidence_id),
            "support": "documented_only",
        },
    )
    db.add(pattern)
    await db.flush()
    await _record(db, tenant_id, pattern.id, case, confidence=0.6)
    logger.info(
        "knowledge_case.seeded_pattern",
        case_id=str(case.id),
        pattern_id=str(pattern.id),
    )
    return {"status": "seeded", "pattern_id": str(pattern.id)}


async def pattern_support(
    db: AsyncSession, tenant_id: uuid.UUID, pattern_id: uuid.UUID
) -> dict:
    """The evidence ledger for one pattern, split by epistemic class.

    This is the number a bare `episode_count` could not express: three KB
    articles and nineteen resolved incidents are not the same pattern, and
    a reviewer deciding whether to trust a playbook needs to see which one
    they have.
    """
    rows = (
        await db.execute(
            select(
                PatternEvidence.evidence_class,
                PatternEvidence.support_role,
                PatternEvidence.outcome,
                func.count().label("n"),
            )
            .where(
                PatternEvidence.tenant_id == tenant_id,
                PatternEvidence.pattern_id == pattern_id,
            )
            .group_by(
                PatternEvidence.evidence_class,
                PatternEvidence.support_role,
                PatternEvidence.outcome,
            )
        )
    ).all()

    support: dict = {
        "documented": 0,
        "prescriptive": 0,
        "empirical": 0,
        "empirical_success": 0,
        "empirical_failure": 0,
        "contradicts": 0,
    }
    for evidence_class, support_role, outcome, count in rows:
        support[evidence_class] = support.get(evidence_class, 0) + count
        if support_role == "contradicts_resolution":
            support["contradicts"] += count
        if evidence_class == "empirical" and outcome in ("success", "failure"):
            support[f"empirical_{outcome}"] += count

    # The state a reviewer actually reads. "documented_only" is not a
    # deficiency — it is a pattern that exists because somebody wrote the
    # failure mode down before it happened here.
    if support["empirical"]:
        state = "empirically_supported"
    elif support["documented"] or support["prescriptive"]:
        state = "documented_only"
    else:
        state = "unsupported"
    support["state"] = state
    return support
