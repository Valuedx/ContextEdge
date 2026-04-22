"""Detect contradictions between approved playbooks and KB evidence.

Scan pipeline (post-redesign — see ENTERPRISE_ARCHITECTURE_REVIEW §3.2):

    for playbook in approved playbooks:
        for step in extracted steps of latest published version:
            step_emb = embed(step)                           # 1 cheap embed per step
            candidates = top-K KB evidence by cosine         # HNSW pre-filter
            for kb in candidates:
                if already_scanned(version, kb):             # incremental cursor
                    skip
                if not token_overlap(step, kb):              # second-line gate
                    record skip_token_overlap
                    continue
                if llm_calls_used >= budget:                 # budget cap
                    record skip_budget
                    continue
                confirm via LLM                              # only here does money move
                record result (contradicts | no_contradiction)
                if contradicts:
                    persist Contradiction + notify

Pre-filter pushes candidate volume from ``O(playbooks × kb × steps)`` to
``O(playbooks × K × steps)`` where K defaults to 20. Typical reduction:
90-95% on a mature tenant. Budget cap bounds worst-case spend per beat.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.ai.provider import generate_embedding, llm_complete_json
from contextedge.graph.builder import add_contradicts_edge
from contextedge.models.evidence import EvidenceItem
from contextedge.models.pattern import Contradiction, ContradictionScanState
from contextedge.models.playbook import Playbook, PlaybookVersion
from contextedge.services.event_log_service import append_operational_event
from contextedge.services.notification_service import NotificationType, send_notification

logger = structlog.get_logger()
KB_EVIDENCE_TYPES = ("kb_article", "sop", "documentation")
STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "into",
    "your", "then", "than", "never", "always",
}

# Default candidate pool from the embedding pre-filter. 20 is large enough to
# catch contradictions that don't share surface vocabulary (e.g. KB says
# "disable MFA" and playbook says "require token auth") and small enough that
# per-playbook-step work is bounded.
DEFAULT_TOP_K_CANDIDATES = 20

# Default per-scan LLM-call budget. Reviewer is expected to raise this
# deliberately when a big catch-up is needed after a long pause or a tenant
# onboarding. Lower bound prevents silent LLM-spend blow-ups during incidents.
DEFAULT_SCAN_BUDGET = 1000


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


async def _llm_confirms_contradiction(
    step_text: str,
    kb_text: str,
    *,
    tenant_id: uuid.UUID | None = None,
    db: AsyncSession | None = None,
) -> tuple[bool, str | None]:
    """Confirm contradiction via LLM, using the registry-versioned prompt
    so per-tenant variants + ``llm.usage`` version tagging work."""
    from contextedge.ai.prompts import get_prompt

    prompt = get_prompt("contradiction", tenant_id)
    user = prompt.format_user(step_text=step_text, kb_text=kb_text)
    result = await llm_complete_json(
        user,
        task="classification",
        system_prompt=prompt.system,
        tenant_id=tenant_id,
        db=db,
        prompt_name=prompt.name,
        prompt_version=prompt.version,
    )
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


async def _top_k_kb_candidates(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    step_embedding: list[float],
    k: int,
    domain_id: uuid.UUID | None,
) -> list[EvidenceItem]:
    """Return the top-K KB evidence items closest to the step embedding.

    Uses the HNSW index on ``evidence_items.embedding`` (migration 0021) for
    ~O(log n) nearest-neighbour — the main cost lever of the redesign.
    Evidence without embeddings is excluded; they'd miss the gate anyway.
    """
    stmt = (
        select(EvidenceItem)
        .where(
            EvidenceItem.tenant_id == tenant_id,
            EvidenceItem.evidence_type.in_(KB_EVIDENCE_TYPES),
            EvidenceItem.embedding.is_not(None),
        )
        .order_by(EvidenceItem.embedding.cosine_distance(step_embedding))
        .limit(k)
    )
    if domain_id is not None:
        # Domain-scoped scan — include domain-wide + tenant-wide evidence.
        stmt = stmt.where(
            (EvidenceItem.domain_id == domain_id) | EvidenceItem.domain_id.is_(None)
        )
    return list((await db.execute(stmt)).scalars().all())


async def _load_scan_cursor(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    playbook_version_id: uuid.UUID,
) -> dict[uuid.UUID, datetime]:
    """Return ``evidence_id → last_scanned_at`` for this (version, tenant)."""
    stmt = select(
        ContradictionScanState.evidence_id,
        ContradictionScanState.last_scanned_at,
    ).where(
        ContradictionScanState.tenant_id == tenant_id,
        ContradictionScanState.playbook_version_id == playbook_version_id,
    )
    rows = (await db.execute(stmt)).all()
    return {row[0]: row[1] for row in rows}


async def _record_scan_state(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    playbook_version_id: uuid.UUID,
    evidence_id: uuid.UUID,
    result: str,
    skip_reason: str | None = None,
) -> None:
    """Upsert a row into ``contradiction_scan_state``. Uses the unique
    constraint on ``(playbook_version_id, evidence_id)`` for idempotency."""
    now = datetime.now(timezone.utc)
    existing = (
        await db.execute(
            select(ContradictionScanState).where(
                ContradictionScanState.playbook_version_id == playbook_version_id,
                ContradictionScanState.evidence_id == evidence_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.last_scanned_at = now
        existing.result = result
        existing.skip_reason = skip_reason
        await db.flush()
        return
    row = ContradictionScanState(
        tenant_id=tenant_id,
        playbook_version_id=playbook_version_id,
        evidence_id=evidence_id,
        last_scanned_at=now,
        result=result,
        skip_reason=skip_reason,
    )
    db.add(row)
    await db.flush()


def _needs_rescan(
    evidence: EvidenceItem,
    last_scanned_at: datetime | None,
) -> bool:
    """Return True if this pair's state is stale or missing."""
    if last_scanned_at is None:
        return True
    # Evidence was re-ingested or updated after last scan — re-evaluate.
    ev_updated = getattr(evidence, "updated_at", None) or evidence.ingested_at
    if ev_updated and ev_updated > last_scanned_at:
        return True
    return False


async def scan_contradictions(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    domain_id: uuid.UUID | None = None,
    *,
    top_k: int = DEFAULT_TOP_K_CANDIDATES,
    max_llm_calls: int = DEFAULT_SCAN_BUDGET,
) -> dict[str, Any]:
    """Scan approved playbooks against KB evidence for contradictions.

    Parameters
    ----------
    top_k : int
        Per-step candidate pool size from the embedding pre-filter. Default
        20 — raise only for tenants with extremely heterogeneous KBs.
    max_llm_calls : int
        Hard cap on LLM confirmation calls in this scan. Once reached,
        remaining candidate pairs are recorded with ``result="skipped_budget"``
        and the scan returns with ``budget_exhausted=True``. Default 1000 —
        this bounds worst-case spend at roughly $0.50 per beat on
        gpt-4o-mini class models.
    """
    playbook_stmt = select(Playbook).where(
        Playbook.tenant_id == tenant_id,
        Playbook.lifecycle_state == "approved",
    )
    if domain_id is not None:
        playbook_stmt = playbook_stmt.where(Playbook.domain_id == domain_id)
    playbooks = (await db.execute(playbook_stmt)).scalars().all()

    scanned_pairs = 0          # pairs the LLM actually confirmed
    llm_calls_used = 0         # separate counter — skipped pairs don't count
    token_skips = 0            # pairs short-circuited by token-overlap gate
    cursor_skips = 0           # pairs skipped because already-scanned-recent
    budget_skips = 0           # pairs skipped because budget hit
    contradictions_created = 0
    contradictions_updated = 0
    budget_exhausted = False

    for playbook in playbooks:
        version = await _latest_published_version(db, playbook.id)
        if version is None:
            continue

        step_texts = extract_step_texts(version)
        if not step_texts:
            continue

        cursor = await _load_scan_cursor(
            db, tenant_id=tenant_id, playbook_version_id=version.id,
        )

        for step_text in step_texts:
            try:
                step_embedding = await generate_embedding(
                    step_text,
                    tenant_id=tenant_id,
                    db=db,
                )
            except Exception as exc:
                logger.warning(
                    "contradiction_scan.step_embed_failed",
                    tenant_id=str(tenant_id),
                    playbook_id=str(playbook.id),
                    error=str(exc),
                )
                continue

            candidates = await _top_k_kb_candidates(
                db,
                tenant_id=tenant_id,
                step_embedding=step_embedding,
                k=top_k,
                domain_id=playbook.domain_id,
            )

            for item in candidates:
                last_scanned = cursor.get(item.id)
                if not _needs_rescan(item, last_scanned):
                    cursor_skips += 1
                    continue

                kb_text = item.body_text or item.title or ""
                if not should_compare_contradiction(step_text, kb_text):
                    await _record_scan_state(
                        db,
                        tenant_id=tenant_id,
                        playbook_version_id=version.id,
                        evidence_id=item.id,
                        result="skipped_token_overlap",
                    )
                    token_skips += 1
                    continue

                if llm_calls_used >= max_llm_calls:
                    await _record_scan_state(
                        db,
                        tenant_id=tenant_id,
                        playbook_version_id=version.id,
                        evidence_id=item.id,
                        result="skipped_budget",
                        skip_reason=f"budget={max_llm_calls} reached at this pair",
                    )
                    budget_skips += 1
                    budget_exhausted = True
                    continue

                scanned_pairs += 1
                llm_calls_used += 1
                try:
                    contradicted, reason = await _llm_confirms_contradiction(
                        step_text, kb_text, tenant_id=tenant_id, db=db,
                    )
                except Exception as exc:
                    logger.warning(
                        "contradiction_check_failed",
                        tenant_id=str(tenant_id),
                        playbook_id=str(playbook.id),
                        evidence_id=str(item.id),
                        error=str(exc),
                    )
                    await _record_scan_state(
                        db,
                        tenant_id=tenant_id,
                        playbook_version_id=version.id,
                        evidence_id=item.id,
                        result="skipped_llm_error",
                        skip_reason=str(exc)[:500],
                    )
                    continue

                await _record_scan_state(
                    db,
                    tenant_id=tenant_id,
                    playbook_version_id=version.id,
                    evidence_id=item.id,
                    result="contradicts" if contradicted else "no_contradiction",
                )

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
        "candidate_pairs_scanned": scanned_pairs,
        "llm_calls_used": llm_calls_used,
        "token_skips": token_skips,
        "cursor_skips": cursor_skips,
        "budget_skips": budget_skips,
        "budget_exhausted": budget_exhausted,
        "contradictions_created": contradictions_created,
        "contradictions_updated": contradictions_updated,
    }
