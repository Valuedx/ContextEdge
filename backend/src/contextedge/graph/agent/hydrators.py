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
from contextedge.models.issue_signature import IssueSignature
from contextedge.models.pattern import Pattern
from contextedge.models.playbook import Playbook, PlaybookVersion
from contextedge.models.policy import TenantPolicy
from contextedge.models.session import ResolutionSession
from contextedge.models.tenant import User
from contextedge.search.risk_policy import risk_within_cap
from contextedge.services.evidence_typing import KNOWLEDGE_EVIDENCE_TYPES
from contextedge.services.knowledge_lifecycle import is_current

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
    "issue_signature": IssueSignature,
}

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


_MISSING_TENANT = object()

# Episode states the agent may see. ``approved`` is precedent; a draft is
# REFERENCE ONLY and is labelled as such at hydration (see
# UNAPPROVED_EPISODE_CAVEAT) so the agent can weigh it accordingly rather
# than citing it as settled fact.
#
# ``superseded`` stays out deliberately and is not an oversight: it is the
# state a merge/dedup gives the LOSER, so the corpus holds ~9x more
# superseded episodes than live ones. Admitting them would bury the agent
# in stale duplicates of episodes it can already see, each one a
# near-copy that reads as independent corroboration. Same for any future
# ``rejected``: a human said no, which is a stronger signal than silence.
AGENT_VISIBLE_EPISODE_STATES = frozenset({"approved", "pending_review"})

UNAPPROVED_EPISODE_CAVEAT = (
    "UNAPPROVED DRAFT — reference only. This episode was reconstructed "
    "automatically and no reviewer has confirmed it. Treat it as a lead to "
    "verify, not as established precedent; prefer approved episodes where "
    "they disagree, and say it is unconfirmed if you cite it."
)


def node_is_visible(
    node_type: str,
    obj: Any,
    scope: AgentGraphAccessScope,
    excluded_evidence_policy_ids: set[UUID],
) -> bool:
    # Fail closed: a model without tenant_id is never agent-visible, rather
    # than being assumed to belong to the caller's tenant.
    if getattr(obj, "tenant_id", _MISSING_TENANT) != scope.tenant_id:
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
        if obj.lifecycle_state != "approved":
            return False
        if obj.expiry_at is not None and obj.expiry_at <= datetime.now(UTC):
            return False
        if not risk_within_cap(obj.risk_tier, scope.playbook_risk_cap):
            return False
    elif node_type == "pattern" and not obj.active_flag:
        return False
    elif node_type == "episode" and obj.reviewer_state not in AGENT_VISIBLE_EPISODE_STATES:
        return False
    elif node_type == "evidence":
        # The source system's knowledge lifecycle. Naturally inert for
        # anything that is not knowledge: a ticket has no state, and an
        # absent state serves. A draft nobody approved or an article a human
        # retired must not reach the agent, which cites what it is given.
        if not is_current(getattr(obj, "knowledge_state", None)):
            return False
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
    elif node_type == "decision":
        if obj.status in {"superseded", "reverted"}:
            return False
        # A pending AI-authored decision is an unreviewed diagnosis. It
        # must not steer the next agent until a human review or a
        # recorded outcome moves it past "pending" — otherwise agent
        # output launders itself into agent input.
        if obj.actor_type == "ai" and obj.status == "pending":
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


# Bounded rendering of a playbook's current version into node facts — an
# agent that can see a playbook exists but not what it DOES has to make a
# second round-trip (or worse, guess). Caps keep a 40-step runbook from
# eating the projection's token budget: the playbook budget in maf.v1 is
# 2 nodes, so worst case is ~2 bounded step lists per projection.
PLAYBOOK_STEPS_CAP = 15
PLAYBOOK_STEP_CHARS = 200
PLAYBOOK_TRIGGER_BUDGET = 600
PLAYBOOK_ROLLBACK_CHARS = 300


def playbook_version_facts(version: PlaybookVersion) -> tuple[dict[str, Any], float | None]:
    """(facts, confidence) from the current version. Steps render as
    ordered labels (title / text / action, same precedence the embedding
    text uses); trigger conditions flatten to bounded strings."""
    from contextedge.services.playbook_embedding import _flatten_strings

    # JSONB defends nothing: a corrupt non-list steps value must degrade
    # to "no steps shown", never TypeError the whole projection.
    steps = version.steps if isinstance(version.steps, list) else []
    step_labels: list[str] = []
    for index, step in enumerate(steps[:PLAYBOOK_STEPS_CAP], start=1):
        if isinstance(step, dict):
            # "instruction" included: seeded playbooks store steps as
            # {"order", "instruction"}, and without it every APPROVED playbook
            # — the only kind an agent can see — projected steps_total with an
            # empty step list. The agent knew four steps existed and could
            # read none of them.
            label = (
                step.get("title")
                or step.get("text")
                or step.get("action")
                or step.get("instruction")
            )
        else:
            label = step
        if label:
            # A best-practice step is expert inference, not sourced
            # procedure — the agent must never relay it as if the source
            # material stated it (same rule the UI badge enforces for
            # humans).
            marker = (
                "[best practice] "
                if isinstance(step, dict)
                and step.get("grounding_status") == "non_grounded"
                else ""
            )
            step_labels.append(f"{index}. {marker}{_text(label, PLAYBOOK_STEP_CHARS)}")

    facts: dict[str, Any] = {
        "semantic_version": version.semantic_version,
        "steps_total": len(steps),
        "steps": step_labels,
    }
    triggers = _flatten_strings(version.trigger_conditions, PLAYBOOK_TRIGGER_BUDGET)
    if triggers:
        facts["trigger_conditions"] = triggers
    rollback = _text(version.rollback_notes, PLAYBOOK_ROLLBACK_CHARS)
    if rollback:
        facts["rollback_notes"] = rollback
    return facts, _float(version.playbook_confidence)


# Ordered actions an engineer actually performed, bounded the same way a
# playbook's steps are. An episode without them tells an agent what broke and
# how it ended, but not what anyone DID — so the agent fills the gap with
# generic troubleshooting structure ("verify connectivity using CLI tools")
# instead of the commands that resolved it.
# Measured, not guessed: at 12 x 220 a single episode rendered up to 3,195
# characters and eight of them took 57% of a 25,000-character projection —
# crowding out the playbooks and documentation that rank below them. Steps are
# worth more per character than evidence summaries, but not at any price. Six
# steps is enough to convey the shape of a resolution; a reader who needs the
# full sequence opens the ticket.
EPISODE_STEPS_CAP = 6
EPISODE_STEP_CHARS = 180

# Evidence is the most numerous and least decisive node type in a projection.
# See the evidence branch in hydrate_node for why this is far below the
# 2,000-char default — and why documentation is exempt from it.
EVIDENCE_SUMMARY_CHARS = 400
KNOWLEDGE_SUMMARY_CHARS = 1_600


def episode_step_facts(steps: list[Any]) -> dict[str, Any]:
    """Render an episode's steps into node facts, successful ones first.

    Ordering is deliberate. When the cap bites, the steps worth keeping are
    the ones that worked: a failed attempt is useful context for a human
    reading the whole record, and actively misleading as the first thing an
    agent copies into an answer. Failed steps are kept — labelled — only
    while there is room, because "we tried X and it did not help" is exactly
    what stops the next engineer repeating it.

    NOTE: step text is raw operational narrative and carries customer
    identifiers (hostnames, company names). It belongs in an internal-facing
    profile only. Do not add episodes to a partner- or customer-facing
    profile without redaction — see MAF_NODE_TYPES in profiles.py.
    """
    if not steps:
        return {}

    def sort_key(step: Any) -> tuple[int, int]:
        # Successful first, then the rest, each in recorded order.
        succeeded = 1 if getattr(step, "successful_flag", False) else 0
        return (-succeeded, int(getattr(step, "step_order", 0) or 0))

    labels: list[str] = []
    for step in sorted(steps, key=sort_key)[:EPISODE_STEPS_CAP]:
        text = _text(getattr(step, "text", None), EPISODE_STEP_CHARS)
        if not text:
            continue
        marker = ""
        if getattr(step, "failed_flag", False):
            marker = " [did not work]"
        elif getattr(step, "successful_flag", False):
            marker = " [resolved]"
        observation = _text(getattr(step, "observation", None), 120)
        suffix = f" -> {observation}" if observation else ""
        labels.append(f"{len(labels) + 1}. {text}{suffix}{marker}")

    if not labels:
        return {}
    return {"steps_total": len(steps), "steps_taken": labels}


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
        # verification_status / verified_at included: an agent weighing a past
        # execution needs to know whether the fix actually HELD — "completed"
        # and "completed, then verified stable" are different precedents, and
        # omitting the verification fields collapsed them. The JSONB
        # verification_details stays out of the projection (unbounded);
        # the status and timestamp are the decision-relevant part.
        facts = _facts(
            obj,
            "status",
            "automation_mode",
            "max_safety_class",
            "outcome",
            "verification_status",
            "verified_at",
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
        # An unapproved draft is admitted as reference material, so the
        # warning has to travel WITH it. `reviewer_state` alone was already
        # projected and is not enough: "pending_review" is a bare enum the
        # model has to interpret, and it sits among a dozen sibling facts.
        # The label carries the flag because that is what a citation shows
        # a reader, and the caveat states in words what the agent should do
        # about it.
        if obj.reviewer_state != "approved":
            label = f"[UNAPPROVED DRAFT] {obj.title or ''}".strip()
        # `primary_case_ref` is the ticket this episode was reconstructed
        # from (INC0009002, RITM0000004). Without it an agent can name an
        # episode but never the record behind it, so an engineer has nothing
        # to open and check — and a cited-but-unverifiable answer is exactly
        # how a plausible wrong answer survives review.
        facts = _facts(
            obj,
            "primary_case_ref",
            "status",
            "reviewer_state",
            "final_outcome",
        )
        if obj.reviewer_state != "approved":
            facts["agent_caveat"] = UNAPPROVED_EPISODE_CAVEAT
        confidence = _float(obj.extraction_confidence)
        # C6: an agent consuming an episode must see that its sources
        # DISAGREED (P4 preserved the accounts; the projection was
        # silently dropping them). Bounded: 3 contradictions, topic +
        # truncated claims only — the review surface has the full record.
        contradictions = getattr(obj, "contradictions", None)
        if contradictions:
            rendered = []
            for entry in contradictions[:3]:
                if not isinstance(entry, dict):
                    continue
                rendered.append(
                    {
                        "topic": _text(entry.get("topic"), limit=120),
                        "accounts": [
                            _text(a.get("claim"), limit=160)
                            for a in (entry.get("accounts") or [])[:3]
                            if isinstance(a, dict)
                        ],
                    }
                )
            if rendered:
                facts["contradictions"] = rendered
        confidence = _float(obj.extraction_confidence)
    elif node_type == "evidence":
        label = obj.title or f"Evidence {str(obj.id)[:8]}"
        # Two kinds of evidence share this table and want opposite budgets.
        #
        # A ticket, a Slack message or a log line CORROBORATES: it establishes
        # that something happened and roughly what was said. 400 characters is
        # plenty, and keeping it short is what leaves room for the playbook and
        # episode steps that actually answer the question.
        #
        # An SOP, KB article or product manual IS the answer. It is vendor-
        # authored, carries no customer data, and is often the only source for
        # a question no incident has ever covered — a compatibility matrix, a
        # required config key, a defect fixed in a later release. Truncating a
        # procedure to 400 characters cuts it mid-sequence, which is the same
        # failure the document chunker exists to avoid: you return the step and
        # lose the warning attached to it.
        is_knowledge = str(getattr(obj, "evidence_type", "") or "") in KNOWLEDGE_EVIDENCE_TYPES
        summary = _text(
            obj.body_summary,
            KNOWLEDGE_SUMMARY_CHARS if is_knowledge else EVIDENCE_SUMMARY_CHARS,
        )
        facts = _facts(
            obj,
            "evidence_type",
            "source_type",
            "evidence_time",
            "relevance_state",
            "relevance_score",
        )
        if is_knowledge:
            # Say so explicitly. In a node list an SOP section and a Slack
            # message are both "evidence", and an agent weighing them has no
            # way to tell that one is normative and the other is hearsay.
            facts["knowledge"] = True
            facts["authority"] = "documented procedure"
        # E2 (roadmap): applicability constraints travel WITH the node so
        # the agent can rule out a fix that does not match the incident's
        # product version or environment — mis-applied remediation being
        # the classic agent failure. Selected keys only; the extractor's
        # raw dict can carry scratch fields.
        applicability = getattr(obj, "applicability", None)
        if applicability:
            compact = {
                key: _value(applicability[key])
                for key in (
                    "product",
                    "product_versions",
                    "version_floor",
                    "version_ceiling",
                    "environments",
                    "components",
                )
                if applicability.get(key)
            }
            if compact:
                facts["applicability"] = compact
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
        # C2: criticality / owning group / CI class live in the JSONB
        # attributes (written by CMDB topology caching and reference
        # enrichment). Selected keys only — the raw attributes blob is
        # unbounded and carries snapshot internals the agent must not see.
        attrs = getattr(obj, "attributes", None) or {}
        for key in ("criticality", "support_group", "ci_class", "monitoring_sources"):
            if attrs.get(key):
                facts[key] = _value(attrs[key])
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
    elif node_type == "issue_signature":
        # ~60 chars of pure diagnostic structure. Label reads as
        # "component: failure mode" so a node list scans like a
        # differential-diagnosis sheet.
        component = (obj.failing_component or obj.affected_capability or "").replace("_", " ")
        mode = (obj.failure_mode or "").replace("_", " ")
        label = f"{component}: {mode}" if component else mode
        summary = None
        facts = _facts(
            obj,
            "affected_capability",
            "failing_component",
            "failure_mode",
            "trigger_change",
            "environment",
            "scope",
            "episode_count",
        )
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
