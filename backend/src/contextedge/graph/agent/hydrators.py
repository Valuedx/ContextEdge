"""Typed, allowlisted node hydration for agent graph projections."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from contextedge.graph.agent.contracts import (
    AgentGraphAccessScope,
    GraphNodeRef,
    HydratedGraphNode,
)
from contextedge.models.action_policy import ActionPolicy
from contextedge.models.case_outcome import CaseOutcome
from contextedge.models.claim import Claim
from contextedge.models.decision import Decision, DecisionOption, DecisionOutcome
from contextedge.models.entity import Entity
from contextedge.models.episode import CanonicalIdentity, Episode
from contextedge.models.error_signature import ErrorSignature, FixPattern
from contextedge.models.evidence import EvidenceItem
from contextedge.models.execution import ApprovalRequest, ExecutionRun
from contextedge.models.pattern import Pattern
from contextedge.models.playbook import Playbook
from contextedge.models.policy import TenantPolicy
from contextedge.models.session import ResolutionSession
from contextedge.models.tenant import User

NODE_MODELS: dict[str, type[Any]] = {
    "session": ResolutionSession,
    "decision": Decision,
    "decision_option": DecisionOption,
    "decision_outcome": DecisionOutcome,
    "approval_request": ApprovalRequest,
    "execution_run": ExecutionRun,
    "playbook": Playbook,
    "pattern": Pattern,
    "episode": Episode,
    "evidence": EvidenceItem,
    "identity": CanonicalIdentity,
    "entity": Entity,
    "user": User,
    "tenant_policy": TenantPolicy,
    "action_policy": ActionPolicy,
    "claim": Claim,
    "error_signature": ErrorSignature,
    "fix_pattern": FixPattern,
    "case_outcome": CaseOutcome,
}

_RISK_ORDER = {"low": 0, "medium": 1, "high": 2, "restricted": 3}


def _value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, Decimal):
        return round(float(value), 6)
    if isinstance(value, (datetime, UUID)):
        return value.isoformat() if isinstance(value, datetime) else str(value)
    if isinstance(value, list):
        return [_value(item) for item in value[:8]]
    return str(value)[:1_000]


def _facts(obj: Any, *names: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name in names:
        value = getattr(obj, name, None)
        if value is not None:
            result[name] = _value(value)
    return result


def _text(value: Any, limit: int = 2_000) -> str | None:
    if value is None:
        return None
    compact = " ".join(str(value).split())
    return compact[:limit] or None


def _float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def node_is_visible(
    node_type: str,
    obj: Any,
    scope: AgentGraphAccessScope,
    excluded_evidence_policy_ids: set[UUID],
) -> bool:
    if getattr(obj, "tenant_id", scope.tenant_id) != scope.tenant_id:
        return False

    domain_id = getattr(obj, "domain_id", None)
    if scope.domain_id is not None and domain_id not in (None, scope.domain_id):
        return False
    if (
        scope.allowed_domain_ids is not None
        and domain_id is not None
        and domain_id not in scope.allowed_domain_ids
    ):
        return False

    workspace_id = getattr(obj, "workspace_id", None)
    if scope.workspace_ids and workspace_id is not None and workspace_id not in scope.workspace_ids:
        return False

    if node_type == "playbook":
        if obj.lifecycle_state != "approved" or obj.current_version_id is None:
            return False
        if obj.expiry_at is not None and obj.expiry_at <= datetime.now(UTC):
            return False
        if _RISK_ORDER.get(obj.risk_tier, 99) > _RISK_ORDER.get(scope.playbook_risk_cap, 2):
            return False
    elif node_type == "pattern" and not obj.active_flag:
        return False
    elif node_type == "episode" and obj.reviewer_state != "approved":
        return False
    elif node_type == "evidence":
        if obj.sensitivity_label == "legal_hold":
            return False
        if obj.redaction_status in {"pending", "pending_redaction"}:
            return False
        if obj.access_policy_id in excluded_evidence_policy_ids:
            return False
    elif node_type == "claim" and obj.validation_status not in {
        "machine_verified",
        "human_validated",
    }:
        return False
    elif node_type == "decision" and obj.status in {"superseded", "reverted"}:
        return False
    elif node_type in {
        "identity",
        "entity",
        "tenant_policy",
        "action_policy",
        "error_signature",
        "fix_pattern",
    } and not getattr(obj, "is_active", True):
        return False
    return True


def hydrate_node(node_type: str, obj: Any) -> HydratedGraphNode:
    label: str
    summary: str | None
    facts: dict[str, Any]
    confidence: float | None = None
    freshness: float | None = None

    if node_type == "session":
        label = obj.title or obj.case_number or f"Session {str(obj.id)[:8]}"
        summary = _text(obj.description or obj.notes)
        facts = _facts(
            obj,
            "case_number",
            "case_type",
            "issue_type",
            "status",
            "priority",
            "severity",
            "environment",
            "closed_at",
        )
    elif node_type == "decision":
        label = obj.decision_summary or obj.decision_type.replace("_", " ")
        summary = _text(obj.rationale_summary)
        facts = _facts(
            obj,
            "decision_type",
            "decision_intent",
            "agent_step",
            "actor_type",
            "status",
            "risk_level",
            "policy_result",
            "approval_required",
            "human_override",
        )
        confidence = _float(obj.confidence)
    elif node_type == "decision_option":
        label = obj.action
        summary = _text(obj.rejection_reason)
        facts = _facts(
            obj,
            "action",
            "suitability",
            "risk_level",
            "preconditions",
            "selected",
            "rejection_code",
        )
        confidence = _float(obj.suitability)
    elif node_type == "decision_outcome":
        label = obj.action_executed
        summary = _text(obj.feedback_received)
        facts = _facts(
            obj,
            "execution_result",
            "follow_up_needed",
            "feedback_code",
        )
    elif node_type == "approval_request":
        label = obj.action_name or obj.requested_action
        summary = _text(obj.approval_note or obj.decision_comment)
        facts = _facts(
            obj,
            "status",
            "safety_class",
            "approver_role",
            "approval_channel",
            "sod_check_status",
            "decided_at",
        )
    elif node_type == "execution_run":
        label = f"Execution {str(obj.id)[:8]}"
        summary = _text(obj.outcome_summary)
        facts = _facts(
            obj,
            "status",
            "automation_mode",
            "max_safety_class",
            "outcome",
            "started_at",
            "completed_at",
        )
    elif node_type == "playbook":
        label = obj.title
        summary = _text(obj.description)
        facts = _facts(
            obj,
            "stable_key",
            "lifecycle_state",
            "risk_tier",
            "automation_mode",
            "last_validated_at",
            "expiry_at",
        )
    elif node_type == "pattern":
        label = obj.title
        summary = _text(obj.description)
        facts = _facts(
            obj,
            "pattern_type",
            "episode_count",
            "contradiction_score",
            "trigger_conditions",
            "core_entities",
            "observed_errors",
            "root_causes",
            "resolution_steps",
        )
        confidence = _float(obj.confidence)
        freshness = _float(obj.freshness_score)
    elif node_type == "episode":
        label = obj.title
        summary = _text(obj.root_cause_summary or obj.final_outcome)
        facts = _facts(obj, "status", "reviewer_state", "final_outcome")
        confidence = _float(obj.extraction_confidence)
    elif node_type == "evidence":
        label = obj.title or f"Evidence {str(obj.id)[:8]}"
        summary = _text(obj.body_summary)
        facts = _facts(
            obj,
            "evidence_type",
            "source_type",
            "evidence_time",
            "relevance_state",
            "relevance_score",
        )
        confidence = _float(obj.relevance_score)
    elif node_type == "identity":
        label = obj.canonical_name
        summary = None
        facts = _facts(obj, "entity_type", "is_active")
    elif node_type == "entity":
        label = obj.name
        summary = None
        facts = _facts(
            obj,
            "entity_type",
            "environment",
            "business_unit",
            "data_domain",
            "is_active",
            "last_synced_at",
        )
        confidence = _float(obj.confidence)
    elif node_type == "user":
        label = obj.display_name
        summary = None
        facts = _facts(obj, "status")
    elif node_type == "tenant_policy":
        label = obj.name
        summary = _text(obj.description)
        facts = _facts(obj, "policy_type", "is_active")
    elif node_type == "action_policy":
        label = obj.policy_name
        summary = _text(obj.description)
        facts = _facts(
            obj,
            "action_name",
            "environment",
            "business_unit",
            "data_domain",
            "risk_level",
            "policy_result",
            "allowed_execution_mode",
            "priority",
            "policy_scope",
            "conflict_resolution",
            "is_active",
        )
    elif node_type == "claim":
        label = obj.claim_type.replace("_", " ")
        summary = _text(obj.claim_text)
        facts = _facts(
            obj,
            "claim_type",
            "created_by_type",
            "validation_status",
            "validated_at",
        )
        confidence = _float(obj.confidence)
    elif node_type == "error_signature":
        label = obj.display_name or obj.signature_key
        summary = _text(obj.normalized_message)
        facts = _facts(
            obj,
            "error_type",
            "usual_causes",
            "recommended_actions",
            "risk_notes",
            "success_count",
            "failure_count",
        )
        confidence = _float(obj.confidence)
    elif node_type == "fix_pattern":
        label = obj.pattern_name
        summary = _text(obj.recommended_fix)
        facts = _facts(
            obj,
            "issue_type",
            "failed_step",
            "preconditions",
            "risk_level",
            "approval_required",
            "success_count",
            "failure_count",
            "last_used_at",
        )
        confidence = _float(obj.confidence)
    elif node_type == "case_outcome":
        label = obj.outcome_status.replace("_", " ")
        summary = _text(obj.resolution_summary)
        facts = _facts(
            obj,
            "outcome_status",
            "confirmed_root_cause",
            "successful_action",
            "failed_actions",
            "user_confirmed",
            "mttr_minutes",
            "closed_at",
        )
    else:
        raise ValueError(f"No hydrator registered for node type: {node_type}")

    return HydratedGraphNode(
        ref=GraphNodeRef(type=node_type, id=obj.id),
        label=_text(label, 500) or node_type,
        summary=summary,
        facts=facts,
        confidence=confidence,
        freshness=freshness,
        created_at=getattr(obj, "created_at", None),
        updated_at=getattr(obj, "updated_at", None),
    )
