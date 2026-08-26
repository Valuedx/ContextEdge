"""Composition root for the diagnose agent host."""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.deps import CurrentUser
from contextedge.graph.agent.contracts import AgentGraphBudget, AgentGraphRequest, GraphNodeRef
from contextedge.graph.agent.service import (
    AgentGraphProjectionService,
    build_agent_graph_scope,
)
from contextedge.integrations.maf.client import (
    InProcessChangeRiskClient,
    InProcessCmdbTopologyClient,
    InProcessCohortClient,
    InProcessContextGraphClient,
    InProcessDecisionWritebackClient,
    InProcessEdgeProposalClient,
    InProcessFixApplicabilityClient,
)
from contextedge.integrations.maf.playbook_client import InProcessPlaybookRetrievalClient
from contextedge.integrations.maf.prompts import DIAGNOSE_SYSTEM_PROMPT
from contextedge.search.hybrid_ranker import rank_playbooks
from contextedge.search.risk_policy import effective_max_risk_tier
from contextedge.services.case_frame_service import CaseFrame, build_case_frame

logger = structlog.get_logger()

MAF_BUDGET = AgentGraphBudget(
    max_nodes=60,
    max_relationships=120,
    max_depth=3,
    max_characters=30_000,
)


class TenantBoundSessionFactory:
    """Opens a fresh session per tool/write-back call with RLS bound."""

    def __init__(self, tenant_id: UUID):
        self.tenant_id = tenant_id

    def __call__(self):
        return _TenantBoundSession(self.tenant_id)


class _TenantBoundSession:
    def __init__(self, tenant_id: UUID):
        self.tenant_id = tenant_id
        self._cm = None

    async def __aenter__(self):
        from contextedge.database import async_session_factory
        from contextedge.tenant_rls import bind_session_tenant

        self._cm = async_session_factory()
        db = await self._cm.__aenter__()
        await bind_session_tenant(db, self.tenant_id, bypass=False)
        return db

    async def __aexit__(self, exc_type, exc, tb):
        return await self._cm.__aexit__(exc_type, exc, tb)


@dataclass(slots=True)
class DiagnoseBundle:
    graph_client: InProcessContextGraphClient
    playbook_client: InProcessPlaybookRetrievalClient
    plugin: Any | None
    writeback: Any | None
    frame: CaseFrame
    scope: Any


def _seeds_from_frame(frame: CaseFrame) -> list[GraphNodeRef]:
    seeds: list[GraphNodeRef] = []
    if frame.error_signature_id:
        seeds.append(GraphNodeRef(type="error_signature", id=frame.error_signature_id))
    if frame.issue_signature_id:
        seeds.append(GraphNodeRef(type="issue_signature", id=frame.issue_signature_id))
    for entity_id in frame.ci_entity_ids[:8]:
        seeds.append(GraphNodeRef(type="entity", id=entity_id))
    return seeds[:20]


def _playbook_client_for(
    db: AsyncSession,
    user: CurrentUser,
    *,
    domain_id: UUID | None,
) -> InProcessPlaybookRetrievalClient:
    return InProcessPlaybookRetrievalClient(
        db,
        user.tenant_id,
        domain_id=domain_id,
        max_risk_tier=effective_max_risk_tier(user),
        allowed_domain_ids=user.allowed_domain_ids,
        caller_roles=user.roles,
    )


async def build_diagnose_bundle(
    db: AsyncSession,
    user: CurrentUser,
    *,
    symptoms: list[str],
    entities: list[str],
    environment: dict | None,
    context: str | None,
    domain_id: UUID | None,
    session_id: UUID | None,
    session_factory=None,
) -> DiagnoseBundle:
    scope = await build_agent_graph_scope(db, user, domain_id)
    frame = build_case_frame(
        symptoms=symptoms,
        entities=entities,
        context=context,
        environment=environment,
        domain_id=domain_id,
    )
    graph_client = InProcessContextGraphClient(AgentGraphProjectionService(db), scope)
    playbook_client = _playbook_client_for(db, user, domain_id=domain_id)
    factory = session_factory or TenantBoundSessionFactory(user.tenant_id)
    writeback = InProcessDecisionWritebackClient(
        factory,
        user.tenant_id,
        user.user_id,
        session_id=session_id,
        domain_id=domain_id,
    )
    plugin = None
    try:
        from contextedge.integrations.maf.plugin import ContextGraphMAFPlugin

        plugin = ContextGraphMAFPlugin(
            graph_client,
            cmdb_client=InProcessCmdbTopologyClient(factory, user.tenant_id),
            change_risk_client=InProcessChangeRiskClient(factory, user.tenant_id),
            fix_applicability_client=InProcessFixApplicabilityClient(
                factory, user.tenant_id
            ),
            cohort_client=InProcessCohortClient(factory, user.tenant_id),
            edge_proposal_client=InProcessEdgeProposalClient(
                factory, user.tenant_id, domain_id=domain_id
            ),
            writeback=writeback,
            playbook_client=playbook_client,
        )
    except ImportError:
        plugin = None
    return DiagnoseBundle(
        graph_client=graph_client,
        playbook_client=playbook_client,
        plugin=plugin,
        writeback=writeback,
        frame=frame,
        scope=scope,
    )


def _as_match_list(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        return list(payload.get("results") or [])
    return list(payload or [])


async def run_playbook_tool_turn(
    client: Any,
    *,
    symptoms: list[str],
    entities: list[str],
    environment: dict | None,
    top_k: int,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None, list[dict[str, Any]]]:
    """Prescribed diagnose tool sequence: match → trigger → get → NK."""
    tool_calls: list[dict[str, Any]] = []
    match_fn = getattr(client, "match_playbooks", None)
    if not callable(match_fn):
        return [], None, tool_calls
    raw_matches = await match_fn(
        symptoms, entities, dict(environment or {}), int(top_k)
    )
    matches = _as_match_list(raw_matches)
    tool_calls.append({"tool": "match_playbooks", "result_count": len(matches)})
    chosen = matches[0] if matches else None
    if not chosen:
        return matches, None, tool_calls
    version_raw = chosen.get("playbook_version_id")
    playbook_raw = chosen.get("playbook_id")
    if not version_raw or not playbook_raw:
        return matches, chosen, tool_calls
    version_id = UUID(str(version_raw))
    playbook_id = UUID(str(playbook_raw))
    trigger = await client.check_trigger_conditions(
        version_id, dict(environment or {}), symptoms
    )
    level = trigger.get("level") if isinstance(trigger, dict) else None
    tool_calls.append({"tool": "check_trigger_conditions", "level": level})
    if isinstance(trigger, dict) and (
        trigger.get("drop") or trigger.get("level") == "contradicted"
    ):
        return matches, {**chosen, "dropped": True, "trigger": trigger}, tool_calls
    playbook = await client.get_playbook(playbook_id, version_id)
    tool_calls.append(
        {"tool": "get_playbook", "ok": "error" not in (playbook or {})}
    )
    negative = await client.get_negative_knowledge(version_id)
    items = (negative or {}).get("items") if isinstance(negative, dict) else []
    tool_calls.append(
        {"tool": "get_negative_knowledge", "item_count": len(items or [])}
    )
    return (
        matches,
        {
            **chosen,
            "trigger": trigger,
            "steps": (playbook or {}).get("steps") if isinstance(playbook, dict) else None,
            "negative_knowledge": negative,
        },
        tool_calls,
    )


async def _rationale_from_prompt(
    *,
    db: AsyncSession,
    user: CurrentUser,
    symptoms: list[str],
    chosen: dict[str, Any] | None,
    grounding: str,
    cited: list[str],
    fallback: str,
) -> str:
    title = (chosen or {}).get("playbook_title") or (chosen or {}).get("title")
    version_id = (chosen or {}).get("playbook_version_id") or "none"
    applicability = (chosen or {}).get("applicability") or "abstain"
    user_prompt = (
        f"Symptoms: {symptoms}\n"
        f"grounding_status={grounding}\n"
        f"chosen_title={title}\n"
        f"chosen_playbook_version_id={version_id}\n"
        f"applicability={applicability}\n"
        f"cited_node_keys={','.join(cited) or 'none'}\n"
        "Write a short operational rationale. End with the structured tail."
    )
    try:
        from contextedge.ai.provider import llm_complete

        text = await llm_complete(
            user_prompt,
            task="diagnose",
            system_prompt=DIAGNOSE_SYSTEM_PROMPT,
            tenant_id=user.tenant_id,
            db=db,
            prompt_name="diagnose_system",
            max_tokens=800,
        )
        if text and str(text).strip():
            return str(text).strip()
    except Exception:
        logger.info("diagnose.llm_rationale_skipped", tenant_id=str(user.tenant_id))
    tail = (
        f"\nchosen_playbook_version_id={version_id}\n"
        f"cited_node_keys={','.join(cited) or 'none'}\n"
        f"applicability={applicability}"
    )
    return fallback + tail


async def _writeback_diagnosis(
    bundle: DiagnoseBundle,
    *,
    rationale: str,
    cited: list[str],
    grounding: str,
    chosen: dict[str, Any] | None,
    selection_margin: float | None,
    query: str,
    subset: Any,
    tool_calls: list[dict[str, Any]],
) -> None:
    version_id = (chosen or {}).get("playbook_version_id")
    applicability = (chosen or {}).get("applicability") or "abstain"
    state = {
        "case_frame": bundle.frame,
        "contextedge_projection": {
            "query": query[:2_000],
            "projection_id": str(getattr(subset, "projection_id", "")),
            "cited_nodes": cited,
            "grounding_status": grounding,
            "warnings": list(getattr(subset, "warnings", None) or []),
            "truncation_reasons": list(getattr(subset, "truncation_reasons", None) or []),
            "chosen_playbook_version_id": version_id,
            "applicability": applicability,
            "selection_margin": selection_margin,
            "tool_calls": tool_calls,
        },
        "chosen_playbook_version_id": version_id,
        "cited_node_keys": cited,
        "applicability": applicability,
        "selection_margin": selection_margin,
    }
    response = SimpleNamespace(text=rationale)
    provider = getattr(getattr(bundle, "plugin", None), "provider", None)
    if provider is not None and getattr(provider, "after_run", None):
        await provider.after_run(
            agent=None,
            session=None,
            context=None,
            state=state,
            response=response,
        )
        return
    writeback = getattr(bundle, "writeback", None)
    if writeback is None:
        return
    await writeback.record_decision(
        {
            "decision_type": "agent_diagnosis",
            "agent_step": "maf_run",
            "rationale_summary": " ".join(rationale.split())[:2_000],
            "context_snapshot": state["contextedge_projection"],
            "confidence": (chosen or {}).get("confidence_calibrated"),
            "approval_required": True,
            "evidence_refs": [
                {
                    "ref_type": key.partition(":")[0],
                    "ref_id": key.partition(":")[2],
                    "description": "cited in the diagnose turn",
                }
                for key in cited[:40]
                if ":" in key
            ],
        }
    )


async def run_diagnose(
    db: AsyncSession,
    user: CurrentUser,
    *,
    symptoms: list[str],
    entities: list[str],
    environment: dict | None = None,
    context: str | None = None,
    domain_id: UUID | None = None,
    session_id: UUID | None = None,
    top_k: int = 5,
    session_factory=None,
) -> dict[str, Any]:
    """Diagnose turn: playbook tools + graph projection + decision write-back.

    When the MAF extra is installed the plugin (ChatAgent tools + provider)
    is built. The prescribed tool sequence always runs so write-back and
    ``get_playbook`` are exercised even without ``agent_framework``.
    """
    bundle = await build_diagnose_bundle(
        db,
        user,
        symptoms=symptoms,
        entities=entities,
        environment=environment,
        context=context,
        domain_id=domain_id,
        session_id=session_id,
        session_factory=session_factory or TenantBoundSessionFactory(user.tenant_id),
    )
    ranked = await rank_playbooks(
        db,
        tenant_id=user.tenant_id,
        query_text=bundle.frame.symptom_text,
        entities=entities,
        top_k=top_k,
        domain_id=domain_id,
        max_risk_tier=effective_max_risk_tier(user),
        allowed_domain_ids=user.allowed_domain_ids,
        caller_roles=user.roles,
        case_frame=bundle.frame,
        environment=environment,
    )
    tool_client = bundle.playbook_client
    if bundle.plugin is not None and getattr(bundle.plugin, "playbook_toolset", None):
        tool_client = bundle.plugin.playbook_toolset.client
    tool_calls: list[dict[str, Any]] = []
    chosen_from_tools: dict[str, Any] | None = None
    try:
        _matches, chosen_from_tools, tool_calls = await run_playbook_tool_turn(
            tool_client,
            symptoms=symptoms,
            entities=entities,
            environment=environment,
            top_k=top_k,
        )
        del _matches
    except Exception:
        logger.warning(
            "diagnose.tool_turn_failed",
            tenant_id=str(user.tenant_id),
            exc_info=True,
        )
    query = (bundle.frame.symptom_text or " ".join(symptoms))[:4_000]
    subset = await bundle.graph_client.get_agent_subset(
        AgentGraphRequest(
            query=query,
            entities=bundle.frame.lexical_terms[:20],
            seeds=_seeds_from_frame(bundle.frame),
            session_id=session_id,
            domain_id=domain_id,
            profile="maf.v1",
            max_depth=3,
            budget=MAF_BUDGET,
        )
    )
    dropped = bool(chosen_from_tools and chosen_from_tools.get("dropped"))
    top = None if dropped else (ranked[0] if ranked else None)
    if not ranked and not subset.nodes:
        grounding = "no_precedent"
    elif not ranked or dropped or subset.truncated or subset.warnings:
        grounding = "weak"
    else:
        grounding = "grounded"
    cited = [node.key for node in subset.nodes[:40]]
    fallback = (
        f"Selected {top.playbook.title} ({top.applicability})"
        if top
        else "No playbook cleared the recommendation threshold."
    )
    chosen_payload = None
    if top is not None:
        chosen_payload = {
            "playbook_id": str(top.playbook.id),
            "playbook_title": top.playbook.title,
            "playbook_version_id": (
                str(top.playbook_version_id) if top.playbook_version_id else None
            ),
            "applicability": top.applicability,
            "confidence_calibrated": top.confidence_calibrated,
        }
        if chosen_from_tools:
            chosen_payload["steps"] = chosen_from_tools.get("steps")
            chosen_payload["negative_knowledge"] = chosen_from_tools.get(
                "negative_knowledge"
            )
            chosen_payload["trigger"] = chosen_from_tools.get("trigger")
    rationale = await _rationale_from_prompt(
        db=db,
        user=user,
        symptoms=symptoms,
        chosen=chosen_payload,
        grounding=grounding,
        cited=cited,
        fallback=fallback,
    )
    try:
        await _writeback_diagnosis(
            bundle,
            rationale=rationale,
            cited=cited,
            grounding=grounding,
            chosen=chosen_payload,
            selection_margin=top.selection_margin if top else None,
            query=query,
            subset=subset,
            tool_calls=tool_calls,
        )
    except Exception:
        logger.warning(
            "diagnose.writeback_failed",
            tenant_id=str(user.tenant_id),
            exc_info=True,
        )
    return {
        "playbook_id": str(top.playbook.id) if top else None,
        "playbook_version_id": str(top.playbook_version_id)
        if top and top.playbook_version_id
        else None,
        "semantic_version": top.semantic_version if top else None,
        "stable_key": top.playbook.stable_key if top else None,
        "applicability": top.applicability if top else "abstain",
        "applicability_factors": top.applicability_factors if top else None,
        "applicability_differences": top.applicability_differences if top else None,
        "selection_margin": top.selection_margin if top else None,
        "confidence_calibrated": top.confidence_calibrated if top else None,
        "cited_node_keys": cited,
        "grounding_status": grounding,
        "rationale": rationale,
        "warnings": list(subset.warnings),
        "truncation_reasons": list(subset.truncation_reasons),
        "candidates": [
            {
                "playbook_id": str(r.playbook.id),
                "playbook_version_id": str(r.playbook_version_id)
                if r.playbook_version_id
                else None,
                "playbook_title": r.playbook.title,
                "stable_key": r.playbook.stable_key,
                "match_score": round(r.score, 4),
                "applicability": r.applicability,
                    "confidence_calibrated": r.confidence_calibrated,
                    "lifecycle_state": getattr(
                        r.playbook, "lifecycle_state", None
                    )
                    or "approved",
            }
            for r in ranked
        ],
        "tool_calls": tool_calls,
        "agent_mode": "tools",
    }
