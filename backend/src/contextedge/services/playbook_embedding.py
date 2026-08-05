"""Semantic fingerprints for playbooks (migration 0035).

A playbook's title/description rarely contains the symptom vocabulary an
engineer (or agent) actually types — "users can't log in" lives in trigger
conditions and step text, not in "Renew VPN certificate and restart
RADIUS". The embedding text therefore combines the playbook row with its
current version (the latest-created one — ``create_playbook_version``
repoints ``current_version_id`` immediately, before review):
title + description + trigger conditions + step titles.

Best-effort by design: an embedding failure leaves the column NULL and
the playbook keeps working through FTS — mirroring the decision-embedding
precedent. Never let an embedding provider error break a playbook write.
"""

from __future__ import annotations

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.models.playbook import Playbook, PlaybookVersion

logger = structlog.get_logger()

MAX_EMBED_CHARS = 4_000


def _flatten_strings(value, budget: int) -> list[str]:
    """Collect string content from arbitrarily-shaped JSONB (dicts, lists,
    scalars) until the character budget runs out."""
    out: list[str] = []
    remaining = budget

    def walk(v):
        nonlocal remaining
        if remaining <= 0:
            return
        if isinstance(v, str):
            s = v.strip()
            if s:
                out.append(s[:remaining])
                remaining -= len(s)
        elif isinstance(v, dict):
            for item in v.values():
                walk(item)
        elif isinstance(v, (list, tuple)):
            for item in v:
                walk(item)

    walk(value)
    return out


def build_playbook_embedding_text(
    playbook: Playbook,
    version: PlaybookVersion | None,
) -> str:
    parts: list[str] = [playbook.title or ""]
    if playbook.description:
        parts.append(playbook.description)
    if version is not None:
        parts.extend(_flatten_strings(version.trigger_conditions, 1_200))
        for step in (version.steps or [])[:20]:
            if isinstance(step, dict):
                # "instruction" is the seeded-playbook key; without it the
                # embedding text for those playbooks was title-only, so
                # symptom-level queries could not reach them semantically.
                label = (
                    step.get("title")
                    or step.get("text")
                    or step.get("action")
                    or step.get("instruction")
                )
                if label:
                    parts.append(str(label))
    return " ".join(" ".join(parts).split())[:MAX_EMBED_CHARS]


async def embed_playbook(
    db: AsyncSession,
    playbook: Playbook,
    version: PlaybookVersion | None = None,
) -> bool:
    """Compute and store the playbook's embedding. Returns True on success;
    False (with a warning log) on any failure — the caller's transaction
    must never depend on the embedding provider."""
    from contextedge.ai.provider import generate_embedding

    if version is None and playbook.current_version_id is not None:
        version = await db.get(PlaybookVersion, playbook.current_version_id)

    text = build_playbook_embedding_text(playbook, version)
    if not text.strip():
        return False
    try:
        playbook.embedding = await generate_embedding(
            text, tenant_id=playbook.tenant_id, db=db
        )
        return True
    except Exception as exc:
        logger.warning(
            "playbook.embedding_failed",
            tenant_id=str(playbook.tenant_id),
            playbook_id=str(playbook.id),
            error_type=type(exc).__name__,
            error=str(exc),
        )
        return False
