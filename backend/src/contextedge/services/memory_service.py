from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from contextedge.models.decision import Decision
from contextedge.models.episode import CanonicalIdentity, Episode, EvidenceIdentityLink
from contextedge.models.evidence import EvidenceItem
from contextedge.models.execution import ExecutionRun, ExecutionStepRun
from contextedge.models.pattern import GraphEdge, Pattern
from contextedge.models.playbook import Playbook, PlaybookVersion
from contextedge.models.session import ResolutionSession
from contextedge.services.event_log_service import append_operational_event
from contextedge.services.identity_service import identity_ids_from_refs, resolve_identity_ids_for_terms

SHORT_TERM_MEMORY = "short_term"
LONG_TERM_MEMORY = "long_term"
REASONING_MEMORY = "reasoning"
KB_LONG_TERM_TYPES = {"kb_article", "sop", "documentation"}


@dataclass
class RuntimeMemoryContext:
    query_text: str
    short_term: dict[str, Any]
    long_term: dict[str, Any]
    reasoning: dict[str, Any]

    def filters_payload(self) -> dict[str, Any]:
        return {
            "memory_classes": [SHORT_TERM_MEMORY, LONG_TERM_MEMORY, REASONING_MEMORY],
            "memory_summary": {
                SHORT_TERM_MEMORY: self.short_term,
                LONG_TERM_MEMORY: self.long_term,
                REASONING_MEMORY: self.reasoning,
            },
        }


def _dedupe_terms(values: list[str | None]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = (value or "").strip()
        if not text:
            continue
        normalized = " ".join(text.split()).casefold()
        if normalized in seen:
            continue
        seen.add(normalized)
        out.append(" ".join(text.split()))
    return out


def memory_retention_windows(base_retention_days: int) -> dict[str, int]:
    base = max(1, int(base_retention_days))
    return {
        SHORT_TERM_MEMORY: base,
        LONG_TERM_MEMORY: max(base * 6, 180),
        REASONING_MEMORY: max(base * 3, 90),
    }


def classify_evidence_memory_class(evidence: EvidenceItem) -> str:
    if evidence.evidence_type in KB_LONG_TERM_TYPES:
        return LONG_TERM_MEMORY
    refs = evidence.canonical_entity_refs or {}
    if isinstance(refs, dict) and refs.get("identities"):
        return LONG_TERM_MEMORY
    return SHORT_TERM_MEMORY


async def build_runtime_memory_context(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    symptoms: list[str],
    entities: list[str],
    context: str | None = None,
    session_id: uuid.UUID | None = None,
    domain_id: uuid.UUID | None = None,
    top_k: int = 5,
) -> RuntimeMemoryContext:
    session = None
    short_term_session: dict[str, Any] | None = None
    recent_trace_events: list[dict[str, Any]] = []
    reasoning_fragments: list[str] = []

    if session_id is not None:
        session_result = await db.execute(
            select(ResolutionSession)
            .where(
                ResolutionSession.id == session_id,
                ResolutionSession.tenant_id == tenant_id,
            )
            .options(selectinload(ResolutionSession.trace_events))
        )
        session = session_result.scalar_one_or_none()
        if session is not None:
            trace_events = list(session.trace_events or [])
            recent = trace_events[-5:]
            recent_trace_events = [
                {
                    "event_type": event.event_type,
                    "reasoning": event.reasoning,
                    "confidence": event.confidence,
                }
                for event in recent
            ]
            reasoning_fragments = [
                event.reasoning.strip()
                for event in recent
                if event.reasoning and event.reasoning.strip()
            ][:3]
            short_term_session = {
                "session_id": str(session.id),
                "status": session.status,
                "symptom_count": len(session.symptoms or []),
                "entity_count": len(session.entities or []),
                "external_case_ids": list(session.external_case_ids or []),
                "notes_present": bool(session.notes),
            }

    execution_runs: list[ExecutionRun] = []
    if session_id is not None:
        execution_result = await db.execute(
            select(ExecutionRun)
            .where(
                ExecutionRun.tenant_id == tenant_id,
                ExecutionRun.session_id == session_id,
            )
            .options(
                selectinload(ExecutionRun.step_runs).selectinload(ExecutionStepRun.tool_invocations),
                selectinload(ExecutionRun.approval_requests),
            )
            .order_by(ExecutionRun.created_at.desc())
            .limit(3)
        )
        execution_runs = list(execution_result.scalars().all())
    recent_decisions: list[Decision] = []
    if session_id is not None:
        decision_result = await db.execute(
            select(Decision)
            .where(
                Decision.tenant_id == tenant_id,
                Decision.session_id == session_id,
            )
            .order_by(Decision.created_at.desc())
            .limit(5)
        )
        recent_decisions = list(decision_result.scalars().all())

    pending_approval_count = sum(
        1
        for run in execution_runs
        for approval in run.approval_requests or []
        if approval.status == "pending"
    )
    recent_tools = _dedupe_terms(
        [
            invocation.tool_name
            for run in execution_runs
            for step in run.step_runs or []
            for invocation in step.tool_invocations or []
        ]
    )[:5]

    resolved_identity_ids = await resolve_identity_ids_for_terms(db, tenant_id, entities)
    identity_rows: list[CanonicalIdentity] = []
    if resolved_identity_ids:
        identity_result = await db.execute(
            select(CanonicalIdentity).where(
                CanonicalIdentity.tenant_id == tenant_id,
                CanonicalIdentity.id.in_(tuple(resolved_identity_ids)),
            )
        )
        identity_rows = list(identity_result.scalars().all())

    evidence_stmt = select(EvidenceItem).where(EvidenceItem.tenant_id == tenant_id)
    if domain_id is not None:
        evidence_stmt = evidence_stmt.where(
            (EvidenceItem.domain_id == domain_id) | EvidenceItem.domain_id.is_(None)
        )
    if resolved_identity_ids:
        evidence_stmt = (
            select(EvidenceItem)
            .join(EvidenceIdentityLink, EvidenceIdentityLink.evidence_id == EvidenceItem.id)
            .where(
                EvidenceItem.tenant_id == tenant_id,
                EvidenceIdentityLink.identity_id.in_(tuple(resolved_identity_ids)),
            )
        )
        if domain_id is not None:
            evidence_stmt = evidence_stmt.where(
                (EvidenceItem.domain_id == domain_id) | EvidenceItem.domain_id.is_(None)
            )
        evidence_stmt = evidence_stmt.order_by(EvidenceItem.ingested_at.desc()).limit(top_k)
    else:
        evidence_stmt = evidence_stmt.order_by(EvidenceItem.ingested_at.desc()).limit(top_k)
    evidence_result = await db.execute(evidence_stmt)
    recent_evidence = list(evidence_result.scalars().all())

    playbook_count_stmt = select(func.count()).where(
        Playbook.tenant_id == tenant_id,
        Playbook.lifecycle_state == "approved",
    )
    pattern_count_stmt = select(func.count()).where(
        Pattern.tenant_id == tenant_id,
        Pattern.active_flag.is_(True),
    )
    if domain_id is not None:
        playbook_count_stmt = playbook_count_stmt.where(
            (Playbook.domain_id == domain_id) | Playbook.domain_id.is_(None)
        )
        pattern_count_stmt = pattern_count_stmt.where(
            (Pattern.domain_id == domain_id) | Pattern.domain_id.is_(None)
        )
    approved_playbook_count = (await db.execute(playbook_count_stmt)).scalar() or 0
    active_pattern_count = (await db.execute(pattern_count_stmt)).scalar() or 0

    query_terms = list(symptoms) + list(entities)
    if context:
        query_terms.append(context)
    if session is not None:
        query_terms.extend(session.symptoms or [])
        query_terms.extend(session.entities or [])
        if session.notes:
            query_terms.append(session.notes)
    query_terms.extend(identity.canonical_name for identity in identity_rows)

    query_text = " ".join(_dedupe_terms(query_terms))

    short_term = {
        "session": short_term_session,
        "recent_evidence_count": len(recent_evidence),
        "recent_evidence_ids": [str(item.id) for item in recent_evidence],
    }
    long_term = {
        "resolved_identity_count": len(identity_rows),
        "resolved_identities": [
            {
                "id": str(identity.id),
                "name": identity.canonical_name,
                "entity_type": identity.entity_type,
            }
            for identity in identity_rows
        ],
        "approved_playbook_count": int(approved_playbook_count),
        "active_pattern_count": int(active_pattern_count),
    }
    recent_decision_summaries = [
        {
            "id": str(d.id),
            "decision_type": d.decision_type,
            "agent_step": d.agent_step,
            "actor_type": d.actor_type,
            "status": d.status,
            "confidence": d.confidence,
            "compact_trace": d.compact_trace,
            "rationale_summary": (d.rationale_summary or "")[:200],
        }
        for d in recent_decisions
    ]
    reasoning = {
        "trace_event_count": len(recent_trace_events),
        "recent_trace_events": recent_trace_events,
        "execution_run_count": len(execution_runs),
        "pending_approval_count": pending_approval_count,
        "recent_tools": recent_tools,
        "reasoning_fragments": reasoning_fragments,
        "decision_count": len(recent_decisions),
        "recent_decisions": recent_decision_summaries,
    }
    return RuntimeMemoryContext(
        query_text=query_text,
        short_term=short_term,
        long_term=long_term,
        reasoning=reasoning,
    )


async def promote_pattern_memory(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    pattern: Pattern,
    episode_ids: list[uuid.UUID],
) -> dict[str, Any]:
    episode_result = await db.execute(
        select(Episode.entity_refs, Episode.evidence_ids).where(
            Episode.tenant_id == tenant_id,
            Episode.id.in_(tuple(episode_ids)),
        )
    )
    identity_ids: set[uuid.UUID] = set()
    evidence_ids: set[str] = set()
    for entity_refs, linked_evidence_ids in episode_result.all():
        identity_ids.update(identity_ids_from_refs(entity_refs))
        for evidence_id in linked_evidence_ids or []:
            evidence_ids.add(str(evidence_id))

    promotion = {
        "memory_class": LONG_TERM_MEMORY,
        "promoted_from": "episodes",
        "episode_count": len(episode_ids),
        "identity_count": len(identity_ids),
        "evidence_count": len(evidence_ids),
    }
    merged_summary = dict(pattern.evidence_summary or {})
    merged_summary["memory_promotion"] = promotion
    pattern.evidence_summary = merged_summary
    await db.flush()
    await append_operational_event(
        db,
        tenant_id=tenant_id,
        entity_type="pattern",
        entity_id=pattern.id,
        event_type="memory.pattern_promoted",
        payload=promotion,
    )
    return promotion


async def promote_playbook_memory(
    db: AsyncSession,
    *,
    playbook: Playbook,
    version: PlaybookVersion,
    actor_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    identity_edge_count = (
        await db.execute(
            select(func.count()).where(
                GraphEdge.tenant_id == playbook.tenant_id,
                GraphEdge.source_node_type == "playbook",
                GraphEdge.source_node_id == playbook.id,
                GraphEdge.target_node_type == "identity",
                GraphEdge.edge_type == "references_identity",
            )
        )
    ).scalar() or 0

    promotion = {
        "memory_class": LONG_TERM_MEMORY,
        "stable_key": playbook.stable_key,
        "semantic_version": version.semantic_version,
        "lifecycle_state": playbook.lifecycle_state,
        "identity_edge_count": int(identity_edge_count),
        "evidence_ref_count": len(version.evidence_refs or []),
        "promoted_at": datetime.now(UTC).isoformat(),
    }
    await append_operational_event(
        db,
        tenant_id=playbook.tenant_id,
        actor_id=actor_id,
        entity_type="playbook",
        entity_id=playbook.id,
        event_type="memory.playbook_promoted",
        payload=promotion,
    )
    return promotion
