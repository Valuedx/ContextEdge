"""Detect contradictions between approved playbooks and KB evidence."""

from __future__ import annotations

import re
import uuid

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.ai.provider import llm_complete_json
from contextedge.graph.builder import add_contradicts_edge
from contextedge.models.evidence import EvidenceItem
from contextedge.models.pattern import Contradiction
from contextedge.models.playbook import Playbook, PlaybookVersion
from contextedge.services.event_log_service import append_operational_event
from contextedge.services.notification_service import NotificationType, send_notification

logger = structlog.get_logger()
KB_EVIDENCE_TYPES = ("kb_article", "sop", "documentation")
STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "that",
    "this",
    "from",
    "into",
    "your",
    "then",
    "than",
    "never",
    "always",
}


def _tokenize(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", text.lower())
        if len(token) > 2 and token not in STOPWORDS
    }


def should_compare_contradiction(step_text: str, kb_text: str) -> bool:
    step_tokens = _tokenize(step_text)
    kb_tokens = _tokenize(kb_text)
    if not step_tokens or not kb_tokens:
        return False
    overlap = step_tokens & kb_tokens
    return len(overlap) >= 2 or (len(overlap) == 1 and min(len(step_tokens), len(kb_tokens)) <= 4)


def extract_step_texts(version: PlaybookVersion) -> list[str]:
    step_texts: list[str] = []
    for step in version.steps or []:
        if isinstance(step, dict):
            text = (
                step.get("text")
                or step.get("title")
                or step.get("action")
                or step.get("description")
            )
        else:
            text = str(step)
        if text:
            step_texts.append(str(text))
    return step_texts


async def _latest_published_version(
    db: AsyncSession,
    playbook_id: uuid.UUID,
) -> PlaybookVersion | None:
    result = await db.execute(
        select(PlaybookVersion)
        .where(
            PlaybookVersion.playbook_id == playbook_id,
            PlaybookVersion.published_at.is_not(None),
        )
        .order_by(PlaybookVersion.published_at.desc(), PlaybookVersion.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _llm_confirms_contradiction(step_text: str, kb_text: str) -> tuple[bool, str | None]:
    prompt = (
        "Decide whether the knowledge-base text contradicts the operational step.\n"
        "Return JSON with keys contradiction (boolean) and reason (string).\n\n"
        f"Operational step:\n{step_text}\n\n"
        f"Knowledge-base text:\n{kb_text}\n"
    )
    result = await llm_complete_json(prompt, task="classification")
    if not isinstance(result, dict):
        return False, None
    contradiction = bool(result.get("contradiction") or result.get("is_contradiction"))
    reason = result.get("reason")
    return contradiction, str(reason) if reason else None


async def _get_or_create_contradiction(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    source_a_ref: str,
    source_b_ref: str,
    description: str | None,
) -> tuple[Contradiction, bool]:
    existing = (
        await db.execute(
            select(Contradiction).where(
                Contradiction.tenant_id == tenant_id,
                Contradiction.source_a_ref == source_a_ref,
                Contradiction.source_b_ref == source_b_ref,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.description = description
        existing.resolution_status = "unresolved"
        await db.flush()
        return existing, False

    row = Contradiction(
        tenant_id=tenant_id,
        source_a_ref=source_a_ref,
        source_b_ref=source_b_ref,
        contradiction_type="playbook_vs_knowledge_base",
        description=description,
        resolution_status="unresolved",
    )
    db.add(row)
    await db.flush()
    return row, True


async def scan_contradictions(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    domain_id: uuid.UUID | None = None,
) -> dict:
    playbook_stmt = select(Playbook).where(
        Playbook.tenant_id == tenant_id,
        Playbook.lifecycle_state == "approved",
    )
    if domain_id is not None:
        playbook_stmt = playbook_stmt.where(Playbook.domain_id == domain_id)
    playbooks = (await db.execute(playbook_stmt)).scalars().all()

    evidence_stmt = select(EvidenceItem).where(
        EvidenceItem.tenant_id == tenant_id,
        EvidenceItem.evidence_type.in_(KB_EVIDENCE_TYPES),
    )
    if domain_id is not None:
        evidence_stmt = evidence_stmt.where(EvidenceItem.domain_id == domain_id)
    kb_items = (await db.execute(evidence_stmt)).scalars().all()

    scanned_pairs = 0
    contradictions_created = 0
    contradictions_updated = 0

    for playbook in playbooks:
        version = await _latest_published_version(db, playbook.id)
        if version is None:
            continue

        step_texts = extract_step_texts(version)
        domain_kb_items = [
            item
            for item in kb_items
            if playbook.domain_id is None
            or item.domain_id is None
            or item.domain_id == playbook.domain_id
        ]

        for step_text in step_texts:
            for item in domain_kb_items:
                kb_text = item.body_text or item.title or ""
                if not should_compare_contradiction(step_text, kb_text):
                    continue
                scanned_pairs += 1
                try:
                    contradicted, reason = await _llm_confirms_contradiction(step_text, kb_text)
                except Exception as exc:
                    logger.warning(
                        "contradiction_check_failed",
                        tenant_id=str(tenant_id),
                        playbook_id=str(playbook.id),
                        evidence_id=str(item.id),
                        error=str(exc),
                    )
                    continue
                if not contradicted:
                    continue

                contradiction, created = await _get_or_create_contradiction(
                    db,
                    tenant_id=tenant_id,
                    source_a_ref=f"playbook:{playbook.stable_key}:{version.semantic_version}",
                    source_b_ref=f"evidence:{item.id}",
                    description=reason or f"Potential contradiction: {step_text[:120]}",
                )
                await add_contradicts_edge(
                    db,
                    tenant_id,
                    playbook.id,
                    item.id,
                    metadata={"contradiction_id": str(contradiction.id)},
                    domain_id=playbook.domain_id,
                )
                await append_operational_event(
                    db,
                    tenant_id=tenant_id,
                    entity_type="contradiction",
                    entity_id=contradiction.id,
                    event_type="contradiction.detected" if created else "contradiction.updated",
                    payload={
                        "playbook_id": str(playbook.id),
                        "playbook_version": version.semantic_version,
                        "evidence_id": str(item.id),
                        "source_a_ref": contradiction.source_a_ref,
                        "source_b_ref": contradiction.source_b_ref,
                        "description": contradiction.description,
                    },
                )
                if created:
                    contradictions_created += 1
                    await send_notification(
                        db,
                        tenant_id,
                        None,
                        NotificationType.CONTRADICTION_ALERT,
                        f"Contradiction detected for {playbook.title}",
                        reason or "A contradiction was detected between a playbook step and KB evidence.",
                        metadata={
                            "playbook_id": str(playbook.id),
                            "evidence_id": str(item.id),
                            "contradiction_id": str(contradiction.id),
                        },
                    )
                else:
                    contradictions_updated += 1

    return {
        "playbooks_scanned": len(playbooks),
        "kb_items_scanned": len(kb_items),
        "candidate_pairs_scanned": scanned_pairs,
        "contradictions_created": contradictions_created,
        "contradictions_updated": contradictions_updated,
    }
