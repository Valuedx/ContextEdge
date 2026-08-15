"""First-class decision trace service.

Creates, retrieves, and manages structured Decision objects with options,
outcomes, and graph edges — the institutional reasoning memory layer.
"""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy import func as sa_func
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from contextedge.graph.builder import (
    link_decision_chain,
    link_decision_episode,
    link_decision_evidence,
    link_decision_option,
    link_decision_outcome,
    link_decision_pattern,
    link_decision_policy,
)
from contextedge.models.decision import (
    DECISION_INTENTS,
    INTENT_BY_DECISION_TYPE,
    Decision,
    DecisionOption,
    DecisionOutcome,
)
from contextedge.services.event_log_service import append_operational_event
from contextedge.services.memory_service import REASONING_MEMORY
from contextedge.services.session_service import append_trace_event

logger = structlog.get_logger()

_DECISION_EAGER = [
    selectinload(Decision.options),
    selectinload(Decision.outcomes),
]

_REF_TYPE_TO_LINKER = {
    "evidence": link_decision_evidence,
    "episode": link_decision_episode,
    "pattern": link_decision_pattern,
}


async def create_decision(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    decision_type: str,
    agent_step: str,
    rationale_summary: str,
    actor_type: str = "ai",
    actor_id: uuid.UUID | None = None,
    session_id: uuid.UUID | None = None,
    domain_id: uuid.UUID | None = None,
    parent_decision_id: uuid.UUID | None = None,
    context_snapshot: dict | None = None,
    evidence_refs: list[dict] | None = None,
    options: list[dict] | None = None,
    confidence: float | None = None,
    uncertainty_notes: str | None = None,
    compact_trace: str | None = None,
    explanation: str | None = None,
    approval_required: bool = False,
    policy_refs: list[str] | None = None,
    human_override: bool = False,
    status: str = "pending",
    decision_intent: str | None = None,
    risk_level: str | None = None,
) -> Decision:
    evidence_refs = evidence_refs or []
    options = options or []
    policy_refs = policy_refs or []

    # F1. Both columns were provisioned by 0029 and written by nothing.
    # ``decision_intent`` is derived from ``decision_type`` so the governance
    # axis can never drift from the action axis; an explicit argument wins.
    # ``risk_level`` is trace-level — the risk of the path actually taken —
    # so it comes from the SELECTED option, not the riskiest one considered.
    # Unknown decision types and option sets without a selection leave NULL
    # rather than guessing.
    if decision_intent is None:
        decision_intent = INTENT_BY_DECISION_TYPE.get(decision_type)
    elif decision_intent not in DECISION_INTENTS:
        raise ValueError(
            f"decision_intent must be one of {DECISION_INTENTS}, got {decision_intent!r}"
        )
    if risk_level is None:
        risk_level = next(
            (
                opt.get("risk_level")
                for opt in options
                if opt.get("selected") and opt.get("risk_level")
            ),
            None,
        )

    evidence_summary = [
        {
            "ref_type": r.get("ref_type", ""),
            "ref_id": r.get("ref_id", ""),
            "description": r.get("description", ""),
        }
        for r in evidence_refs
    ]

    decision = Decision(
        tenant_id=tenant_id,
        domain_id=domain_id,
        session_id=session_id,
        parent_decision_id=parent_decision_id,
        decision_type=decision_type,
        agent_step=agent_step,
        actor_type=actor_type,
        actor_id=actor_id,
        context_snapshot=context_snapshot or {},
        evidence_summary=evidence_summary,
        rationale_summary=rationale_summary,
        confidence=confidence,
        uncertainty_notes=uncertainty_notes,
        compact_trace=compact_trace,
        explanation=explanation,
        approval_required=approval_required,
        policy_refs=policy_refs,
        human_override=human_override,
        status=status,
        decision_intent=decision_intent,
        risk_level=risk_level,
    )
    db.add(decision)
    await db.flush()

    option_objs: list[DecisionOption] = []
    for opt in options:
        obj = DecisionOption(
            decision_id=decision.id,
            tenant_id=tenant_id,
            action=opt["action"],
            suitability=opt.get("suitability"),
            risk_level=opt.get("risk_level"),
            preconditions=opt.get("preconditions", []),
            rejection_reason=opt.get("rejection_reason"),
            rejection_code=opt.get("rejection_code"),
            selected=opt.get("selected", False),
        )
        db.add(obj)
        option_objs.append(obj)
    if option_objs:
        await db.flush()

    for ref in evidence_refs:
        linker = _REF_TYPE_TO_LINKER.get(ref.get("ref_type", ""))
        if linker and ref.get("ref_id"):
            try:
                ref_uuid = uuid.UUID(ref["ref_id"])
            except (ValueError, AttributeError):
                continue
            await linker(db, tenant_id, decision.id, ref_uuid, domain_id)

    for obj in option_objs:
        await link_decision_option(
            db, tenant_id, decision.id, obj.id, obj.selected, domain_id,
        )

    for pr in policy_refs:
        try:
            policy_uuid = uuid.UUID(pr)
        except (ValueError, AttributeError):
            continue
        await link_decision_policy(db, tenant_id, decision.id, policy_uuid, domain_id)

    if parent_decision_id:
        await link_decision_chain(db, tenant_id, parent_decision_id, decision.id, domain_id)

    if session_id:
        await append_trace_event(
            db,
            tenant_id=tenant_id,
            session_id=session_id,
            event_type=f"decision.{decision_type}",
            inputs=context_snapshot or {},
            outputs={"decision_id": str(decision.id)},
            reasoning=compact_trace or rationale_summary,
            confidence=confidence,
        )

    await append_operational_event(
        db,
        tenant_id=tenant_id,
        entity_type="decision",
        entity_id=decision.id,
        session_id=session_id,
        event_type="decision.created",
        payload={
            "memory_class": REASONING_MEMORY,
            "decision_type": decision_type,
            "agent_step": agent_step,
            "actor_type": actor_type,
            "confidence": confidence,
            "options_count": len(option_objs),
            "evidence_refs_count": len(evidence_refs),
        },
    )

    # Best-effort inline embedding — powers semantic similar-decision retrieval.
    # Failure here must not fail decision creation (LLM provider hiccup, rate
    # limit, network). `find_similar_decisions` already falls back to JSONB
    # containment ordering when a decision has no embedding, so a null here
    # just means this decision participates in structural search until it's
    # re-embedded by a follow-up job.
    try:
        from contextedge.ai.embeddings import embed_decision

        decision.embedding = await embed_decision(
            decision_type=decision_type,
            rationale_summary=rationale_summary,
            compact_trace=compact_trace,
        )
        await db.flush()
    except Exception as exc:
        logger.warning(
            "decision.embed_failed",
            decision_id=str(decision.id),
            error=str(exc),
        )

    from contextedge.services.review_queue_service import invalidate_review_context
    await invalidate_review_context(tenant_id, session_id)

    await db.refresh(decision)
    return decision


async def record_outcome(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    decision_id: uuid.UUID,
    action_executed: str,
    execution_result: str,
    result_details: dict | None = None,
    follow_up_needed: bool = False,
    follow_up_decision_id: uuid.UUID | None = None,
    feedback_received: str | None = None,
    feedback_code: str | None = None,
    feedback_by: uuid.UUID | None = None,
) -> DecisionOutcome | None:
    decision = await get_decision(db, tenant_id=tenant_id, decision_id=decision_id)
    if decision is None:
        return None

    outcome = DecisionOutcome(
        decision_id=decision_id,
        tenant_id=tenant_id,
        action_executed=action_executed,
        execution_result=execution_result,
        result_details=result_details or {},
        follow_up_needed=follow_up_needed,
        follow_up_decision_id=follow_up_decision_id,
        feedback_received=feedback_received,
        feedback_code=feedback_code,
        feedback_by=feedback_by,
    )
    db.add(outcome)
    await db.flush()

    await link_decision_outcome(
        db, tenant_id, decision_id, outcome.id, decision.domain_id,
    )

    if follow_up_decision_id:
        await link_decision_chain(
            db, tenant_id, decision_id, follow_up_decision_id, decision.domain_id,
        )

    if decision.status == "pending":
        decision.status = "completed"
        await db.flush()

    await append_operational_event(
        db,
        tenant_id=tenant_id,
        entity_type="decision",
        entity_id=decision_id,
        session_id=decision.session_id,
        event_type="decision.outcome_recorded",
        payload={
            "memory_class": REASONING_MEMORY,
            "action_executed": action_executed,
            "execution_result": execution_result,
            "follow_up_needed": follow_up_needed,
        },
    )

    from contextedge.services.review_queue_service import invalidate_review_context
    await invalidate_review_context(tenant_id, decision.session_id)

    await db.refresh(outcome)
    return outcome


async def get_decision(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    decision_id: uuid.UUID,
) -> Decision | None:
    result = await db.execute(
        select(Decision)
        .where(Decision.id == decision_id, Decision.tenant_id == tenant_id)
        .options(*_DECISION_EAGER)
    )
    return result.scalar_one_or_none()


LIST_SORT_CHOICES = ("created_desc", "confidence_desc", "confidence_asc")


async def list_decisions(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    session_id: uuid.UUID | None = None,
    decision_type: str | None = None,
    agent_step: str | None = None,
    status: str | None = None,
    actor_type: str | None = None,
    min_confidence: float | None = None,
    max_confidence: float | None = None,
    sort: str = "created_desc",
    limit: int = 50,
    offset: int = 0,
) -> list[Decision]:
    if sort not in LIST_SORT_CHOICES:
        raise ValueError(f"sort must be one of {LIST_SORT_CHOICES}")

    stmt = (
        select(Decision)
        .where(Decision.tenant_id == tenant_id)
        .options(*_DECISION_EAGER)
    )
    if session_id is not None:
        stmt = stmt.where(Decision.session_id == session_id)
    if decision_type is not None:
        stmt = stmt.where(Decision.decision_type == decision_type)
    if agent_step is not None:
        stmt = stmt.where(Decision.agent_step == agent_step)
    if status is not None:
        stmt = stmt.where(Decision.status == status)
    if actor_type is not None:
        stmt = stmt.where(Decision.actor_type == actor_type)
    if min_confidence is not None:
        stmt = stmt.where(Decision.confidence >= min_confidence)
    if max_confidence is not None:
        stmt = stmt.where(Decision.confidence <= max_confidence)

    if sort == "confidence_desc":
        stmt = stmt.order_by(
            Decision.confidence.desc().nullslast(),
            Decision.created_at.desc(),
        )
    elif sort == "confidence_asc":
        stmt = stmt.order_by(
            Decision.confidence.asc().nullslast(),
            Decision.created_at.desc(),
        )
    else:
        stmt = stmt.order_by(Decision.created_at.desc())

    stmt = stmt.limit(limit).offset(offset)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_decision_chain(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    decision_id: uuid.UUID,
    max_depth: int = 20,
) -> list[Decision]:
    """Walk the parent chain upward, then collect children downward."""
    chain: list[Decision] = []
    visited: set[uuid.UUID] = set()

    root = await get_decision(db, tenant_id=tenant_id, decision_id=decision_id)
    if root is None:
        return chain

    current: Decision | None = root
    ancestors: list[Decision] = []
    while current and current.parent_decision_id and len(ancestors) < max_depth:
        if current.parent_decision_id in visited:
            break
        visited.add(current.parent_decision_id)
        current = await get_decision(
            db, tenant_id=tenant_id, decision_id=current.parent_decision_id
        )
        if current:
            ancestors.append(current)

    ancestors.reverse()
    chain.extend(ancestors)

    if root.id not in visited:
        chain.append(root)
        visited.add(root.id)

    queue = [root.id]
    while queue and len(chain) < max_depth:
        parent_id = queue.pop(0)
        children_stmt = (
            select(Decision)
            .where(
                Decision.tenant_id == tenant_id,
                Decision.parent_decision_id == parent_id,
            )
            .options(*_DECISION_EAGER)
            .order_by(Decision.created_at)
        )
        children_result = await db.execute(children_stmt)
        for child in children_result.scalars().all():
            if child.id not in visited:
                visited.add(child.id)
                chain.append(child)
                queue.append(child.id)

    return chain


async def count_similar_decisions(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    decision_type: str,
    context_snapshot: dict | None = None,
) -> int:
    """Count decisions with matching type and overlapping context keys.

    Uses the same filter logic as `find_similar_decisions` so the UI can
    render accurate provenance counts ("based on N similar tickets") without
    retrieving the decision rows.
    """
    stmt = select(sa_func.count()).select_from(Decision).where(
        Decision.tenant_id == tenant_id,
        Decision.decision_type == decision_type,
    )

    if context_snapshot:
        match_keys = {}
        for key in ("workflow", "environment", "impacted_dependency"):
            if key in context_snapshot:
                match_keys[key] = context_snapshot[key]
        if match_keys:
            from sqlalchemy import cast
            from sqlalchemy.dialects.postgresql import JSONB as JSONB_TYPE

            stmt = stmt.where(
                Decision.context_snapshot.op("@>")(cast(match_keys, JSONB_TYPE))
            )

    result = await db.execute(stmt)
    return int(result.scalar_one() or 0)


async def _resolve_query_embedding(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    query_decision_id: uuid.UUID | None,
    query_text: str | None,
) -> list[float] | None:
    """Resolve a query embedding from a decision id or free text.

    Priority: explicit decision id (uses that decision's stored embedding)
    → free text (embeds on the fly) → None (caller falls back to JSONB
    ordering). Swallows embedding failures so retrieval never 500s on a
    provider hiccup — the JSONB fallback still returns results.
    """
    if query_decision_id is not None:
        ref_stmt = select(Decision.embedding).where(
            Decision.id == query_decision_id,
            Decision.tenant_id == tenant_id,
        )
        ref_result = await db.execute(ref_stmt)
        ref_embedding = ref_result.scalar_one_or_none()
        if ref_embedding is not None:
            return list(ref_embedding)
        return None

    if query_text:
        try:
            from contextedge.ai.provider import generate_embedding
            return await generate_embedding(query_text)
        except Exception as exc:
            logger.warning(
                "decision.query_embed_failed",
                error=str(exc),
            )
            return None

    return None


async def find_similar_decisions(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    decision_type: str,
    context_snapshot: dict | None = None,
    query_decision_id: uuid.UUID | None = None,
    query_text: str | None = None,
    limit: int = 10,
) -> list[Decision]:
    """Find decisions with matching type, ordered semantically when possible.

    Resolution order:
    1. A query embedding is resolved from `query_decision_id` (that decision's
       own embedding) or `query_text` (embedded on the fly). When available,
       results are ordered by `embedding <=> query` cosine distance and the
       query is constrained to decisions that have an embedding of their own.
    2. Otherwise, results are ordered by `created_at DESC` as before (no
       regression for callers that don't opt in).

    JSONB containment on `context_snapshot` is applied in both paths so
    `workflow` / `environment` / `impacted_dependency` still act as a
    structural pre-filter alongside the semantic order.
    """
    query_embedding = await _resolve_query_embedding(
        db,
        tenant_id=tenant_id,
        query_decision_id=query_decision_id,
        query_text=query_text,
    )

    stmt = (
        select(Decision)
        .where(
            Decision.tenant_id == tenant_id,
            Decision.decision_type == decision_type,
        )
        .options(*_DECISION_EAGER)
        .limit(limit)
    )

    if query_embedding is not None:
        stmt = stmt.where(Decision.embedding.is_not(None))
        if query_decision_id is not None:
            stmt = stmt.where(Decision.id != query_decision_id)
        from contextedge.search.vector_ops import halfvec_cosine_distance, tune_ann_recall

        await tune_ann_recall(db)
        stmt = stmt.order_by(halfvec_cosine_distance(Decision.embedding, query_embedding))
    else:
        stmt = stmt.order_by(Decision.created_at.desc())

    if context_snapshot:
        match_keys = {}
        for key in ("workflow", "environment", "impacted_dependency"):
            if key in context_snapshot:
                match_keys[key] = context_snapshot[key]
        if match_keys:
            from sqlalchemy import cast
            from sqlalchemy.dialects.postgresql import JSONB as JSONB_TYPE

            stmt = stmt.where(
                Decision.context_snapshot.op("@>")(cast(match_keys, JSONB_TYPE))
            )

    result = await db.execute(stmt)
    return list(result.scalars().all())


async def reject_decision(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    decision_id: uuid.UUID,
    code: str,
    comment: str | None = None,
    actor_id: uuid.UUID | None = None,
) -> DecisionOutcome | None:
    """Reject an AI-recommended decision with a structured reason code.

    Stamps the currently selected option with `rejection_code` + `rejection_reason`
    and `selected=False`, creates a `DecisionOutcome` with
    `execution_result="rejected"` carrying the same code as `feedback_code`,
    flips `decision.status="superseded"` and `human_override=True`, and emits
    an operational event for analytics.
    """
    from contextedge.models.decision import REJECTION_REASON_CODES

    if code not in REJECTION_REASON_CODES:
        raise ValueError(
            f"code must be one of {REJECTION_REASON_CODES}",
        )

    decision = await get_decision(db, tenant_id=tenant_id, decision_id=decision_id)
    if decision is None:
        return None

    for opt in decision.options:
        if opt.selected:
            opt.selected = False
            opt.rejection_code = code
            if comment and not opt.rejection_reason:
                opt.rejection_reason = comment

    decision.status = "superseded"
    decision.human_override = True
    await db.flush()

    outcome = DecisionOutcome(
        decision_id=decision_id,
        tenant_id=tenant_id,
        action_executed="rejected_by_reviewer",
        execution_result="rejected",
        result_details={"code": code},
        follow_up_needed=True,
        feedback_received=comment,
        feedback_code=code,
        feedback_by=actor_id,
    )
    db.add(outcome)
    await db.flush()

    await link_decision_outcome(
        db, tenant_id, decision_id, outcome.id, decision.domain_id,
    )

    await append_operational_event(
        db,
        tenant_id=tenant_id,
        entity_type="decision",
        entity_id=decision_id,
        session_id=decision.session_id,
        actor_id=actor_id,
        event_type="decision.rejected",
        payload={
            "memory_class": REASONING_MEMORY,
            "code": code,
            "comment": comment,
            "decision_type": decision.decision_type,
            "agent_step": decision.agent_step,
        },
    )

    from contextedge.services.review_queue_service import invalidate_review_context
    await invalidate_review_context(tenant_id, decision.session_id)

    await db.refresh(outcome)
    return outcome


async def find_similar_decisions_aggregate(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    decision_type: str,
    context_snapshot: dict | None = None,
    query_decision_id: uuid.UUID | None = None,
    query_text: str | None = None,
    limit: int = 10,
) -> dict:
    """Compose top-N similar decisions + total count + outcome aggregate.

    Calls `find_similar_decisions`, `count_similar_decisions`, and
    `get_decision_effectiveness` with the same filter so the UI can render
    "based on N similar tickets, X% succeeded, here are the top K" in one
    round trip. Returns a dict shaped for `SimilarDecisionsAggregateResponse`.

    `query_decision_id` / `query_text` flow into semantic retrieval via
    `find_similar_decisions`. Count + effectiveness remain scoped by
    `decision_type` + `context_snapshot` only — they're cardinality /
    aggregate metrics over the structural slice, not the semantic ordering.
    """
    decisions = await find_similar_decisions(
        db,
        tenant_id=tenant_id,
        decision_type=decision_type,
        context_snapshot=context_snapshot,
        query_decision_id=query_decision_id,
        query_text=query_text,
        limit=limit,
    )
    total_count = await count_similar_decisions(
        db,
        tenant_id=tenant_id,
        decision_type=decision_type,
        context_snapshot=context_snapshot,
    )
    effectiveness = await get_decision_effectiveness(
        db,
        tenant_id=tenant_id,
        decision_type=decision_type,
        context_filters=context_snapshot,
    )
    outcomes: dict[str, int] = effectiveness.get("outcomes", {}) or {}

    # success_rate = success / sum(counted_outcomes); unknown labels excluded
    # so a rogue label can't skew the denominator. Matches the math used by
    # the review-queue bundle for consistency across consumers.
    counted_keys = {"success", "failure", "partial", "timeout", "rejected"}
    counted = {k: v for k, v in outcomes.items() if k in counted_keys}
    counted_total = sum(counted.values())
    if counted_total > 0:
        success_rate = counted.get("success", 0) / counted_total
    else:
        success_rate = None

    return {
        "decision_type": decision_type,
        "context_filters": context_snapshot or {},
        "total_count": total_count,
        "outcomes": outcomes,
        "success_rate": success_rate,
        "decisions": decisions,
    }


async def get_decision_provenance(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    decision_id: uuid.UUID,
    evidence_limit: int = 20,
    episode_limit: int = 10,
    pattern_limit: int = 10,
) -> dict | None:
    """Hydrate a decision's `based_on` references for Zone 5 provenance.

    Looks up all `based_on` graph edges from this decision, groups targets
    by node type, and returns title + summary + source info + deep-link
    for each evidence citation (and title + status for episodes + patterns).
    Returns `None` if the decision does not exist on this tenant.
    """
    from contextedge.models.episode import Episode
    from contextedge.models.evidence import EvidenceItem
    from contextedge.models.pattern import GraphEdge, Pattern
    from contextedge.models.source import Source, SourceObject
    from contextedge.services.source_deep_link_service import build_source_deep_link

    decision = await get_decision(db, tenant_id=tenant_id, decision_id=decision_id)
    if decision is None:
        return None

    edges_stmt = select(GraphEdge).where(
        GraphEdge.tenant_id == tenant_id,
        GraphEdge.source_node_type == "decision",
        GraphEdge.source_node_id == decision_id,
        GraphEdge.edge_type == "based_on",
    )
    edges = (await db.execute(edges_stmt)).scalars().all()

    evidence_ids: list[uuid.UUID] = []
    episode_ids: list[uuid.UUID] = []
    pattern_ids: list[uuid.UUID] = []
    for e in edges:
        if e.target_node_type == "evidence":
            evidence_ids.append(e.target_node_id)
        elif e.target_node_type == "episode":
            episode_ids.append(e.target_node_id)
        elif e.target_node_type == "pattern":
            pattern_ids.append(e.target_node_id)

    evidence_items: list[dict] = []
    if evidence_ids:
        ev_stmt = (
            select(EvidenceItem, Source, SourceObject)
            .join(Source, Source.id == EvidenceItem.source_id)
            .outerjoin(
                SourceObject, SourceObject.id == EvidenceItem.source_object_id,
            )
            .where(
                EvidenceItem.id.in_(evidence_ids[:evidence_limit]),
                EvidenceItem.tenant_id == tenant_id,
            )
            .order_by(EvidenceItem.ingested_at.desc())
        )
        for ev, src, src_obj in (await db.execute(ev_stmt)).all():
            external_id = src_obj.external_id if src_obj is not None else None
            thread_external = None
            if src_obj is not None and src_obj.metadata_extra:
                thread_external = src_obj.metadata_extra.get("thread_id")
            deep_link = build_source_deep_link(
                src.source_type,
                src.config,
                external_id,
                thread_id=thread_external,
            )
            evidence_items.append({
                "evidence_id": ev.id,
                "title": ev.title or ev.body_summary,
                "body_summary": ev.body_summary,
                "evidence_type": ev.evidence_type,
                "source_id": src.id,
                "source_type": src.source_type,
                "source_display_name": src.display_name,
                "external_id": external_id,
                "deep_link": deep_link,
                "delta_signal": ev.delta_signal,
                "ingested_at": ev.ingested_at,
            })

    episode_items: list[dict] = []
    if episode_ids:
        ep_stmt = (
            select(Episode)
            .where(
                Episode.id.in_(episode_ids[:episode_limit]),
                Episode.tenant_id == tenant_id,
            )
            .order_by(Episode.created_at.desc())
        )
        for ep in (await db.execute(ep_stmt)).scalars().all():
            episode_items.append({
                "episode_id": ep.id,
                "title": ep.title,
                "status": ep.status,
                "final_outcome": ep.final_outcome,
                "extraction_confidence": ep.extraction_confidence,
            })

    pattern_items: list[dict] = []
    if pattern_ids:
        pt_stmt = (
            select(Pattern)
            .where(
                Pattern.id.in_(pattern_ids[:pattern_limit]),
                Pattern.tenant_id == tenant_id,
            )
            .order_by(Pattern.confidence.desc())
        )
        for pt in (await db.execute(pt_stmt)).scalars().all():
            pattern_items.append({
                "pattern_id": pt.id,
                "title": pt.title,
                "pattern_type": pt.pattern_type,
                "confidence": pt.confidence,
                "episode_count": pt.episode_count,
            })

    return {
        "decision_id": decision_id,
        "evidence": evidence_items,
        "episodes": episode_items,
        "patterns": pattern_items,
    }


async def get_decision_effectiveness(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    decision_type: str,
    context_filters: dict | None = None,
) -> dict:
    """Aggregate outcome stats for a decision type/context.

    Returns counts of each execution_result value and a total, enabling
    queries like "is restart usually effective for this failure type?"
    """
    stmt = (
        select(
            DecisionOutcome.execution_result,
            sa_func.count().label("cnt"),
        )
        .join(Decision, DecisionOutcome.decision_id == Decision.id)
        .where(
            Decision.tenant_id == tenant_id,
            Decision.decision_type == decision_type,
        )
        .group_by(DecisionOutcome.execution_result)
    )

    if context_filters:
        match_keys = {}
        for key in ("workflow", "environment", "impacted_dependency"):
            if key in context_filters:
                match_keys[key] = context_filters[key]
        if match_keys:
            from sqlalchemy import cast
            from sqlalchemy.dialects.postgresql import JSONB as JSONB_TYPE

            stmt = stmt.where(
                Decision.context_snapshot.op("@>")(cast(match_keys, JSONB_TYPE))
            )

    result = await db.execute(stmt)
    rows = result.all()

    stats: dict[str, int] = {}
    total = 0
    for row in rows:
        stats[row[0]] = row[1]
        total += row[1]

    return {
        "decision_type": decision_type,
        "context_filters": context_filters or {},
        "total": total,
        "outcomes": stats,
    }
