"""Historical pattern domain audit (backlog C7).

PR #17's domain-safe mining guard stopped NEW cross-domain pattern
membership, with an honest caveat: patterns minted before the guard may
still contain members from other domains. This audit finds them and
FLAGS them for review — it never deletes: which member is wrong (the
pattern's domain or the episode's) is a human call, made through the
existing pattern-link APIs.

Idempotent: re-running re-flags the same violations (operational events
carry the audit timestamp context); fixed patterns simply stop
appearing.
"""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.models.episode import Episode
from contextedge.models.pattern import Pattern, PatternEvidenceLink

logger = structlog.get_logger()

AUDIT_PATTERN_LIMIT = 500


async def audit_pattern_domains(
    db: AsyncSession, tenant_id: uuid.UUID
) -> dict:
    """Flag pattern members whose episode's domain differs from the
    pattern's. NULL-domain episodes are never violations (the global
    mining pass owns them); NULL-domain patterns have nothing to
    violate."""
    counts = {"patterns_checked": 0, "patterns_flagged": 0, "violations": 0}
    flagged: list[dict] = []

    patterns = (
        (
            await db.execute(
                select(Pattern)
                .where(
                    Pattern.tenant_id == tenant_id,
                    Pattern.domain_id.is_not(None),
                )
                .limit(AUDIT_PATTERN_LIMIT)
            )
        )
        .scalars()
        .all()
    )
    for pattern in patterns:
        counts["patterns_checked"] += 1
        rows = (
            await db.execute(
                select(PatternEvidenceLink.episode_id, Episode.domain_id)
                .join(Episode, Episode.id == PatternEvidenceLink.episode_id)
                .where(
                    PatternEvidenceLink.pattern_id == pattern.id,
                    PatternEvidenceLink.episode_id.is_not(None),
                    Episode.tenant_id == tenant_id,
                )
            )
        ).all()
        violations = [
            {"episode_id": str(episode_id), "episode_domain": str(episode_domain)}
            for episode_id, episode_domain in rows
            if episode_domain is not None and episode_domain != pattern.domain_id
        ]
        if not violations:
            continue
        counts["patterns_flagged"] += 1
        counts["violations"] += len(violations)
        flagged.append(
            {
                "pattern_id": str(pattern.id),
                "pattern_domain": str(pattern.domain_id),
                "violations": violations,
            }
        )
        from contextedge.services.event_log_service import append_operational_event

        await append_operational_event(
            db,
            tenant_id=tenant_id,
            entity_type="pattern",
            entity_id=pattern.id,
            event_type="pattern.domain_violation_flagged",
            payload={
                "pattern_domain": str(pattern.domain_id),
                "violations": violations[:20],
                "violation_count": len(violations),
            },
        )
    if counts["patterns_flagged"]:
        logger.warning(
            "pattern_audit.domain_violations",
            tenant_id=str(tenant_id),
            **{k: v for k, v in counts.items()},
        )
    return {**counts, "flagged": flagged[:50]}
