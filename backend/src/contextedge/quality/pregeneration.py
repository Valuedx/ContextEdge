"""Pre-generation quality gates (Phase 2, plan §8).

Runs before playbook generation. Hard-blocking outcomes stop generation;
soft outcomes attach the contract and record gaps rather than letting the
model invent padding.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from contextedge.quality.contract import (
    BLOCKING_OUTCOMES,
    ArtifactType,
    ContractOutcome,
    QualityContract,
)

# Purposes that become contract obligations — never ``context``.
_OBLIGATION_PURPOSES = frozenset({"action", "prerequisite", "validation", "rollback"})

# Component/failure-mode mismatch — drop from obligations. Version mismatch
# stays: the procedure may still apply with an explicit caveat (§8.4).
_INAPPLICABLE_VERDICTS = frozenset({"mismatch"})

_TOKEN_RE = re.compile(r"[a-z0-9]{3,}")


@dataclass
class PreGenerationResult:
    """Outcome of all pre-generation gates."""

    outcome: ContractOutcome
    artifact_type: str
    reasons: list[str] = field(default_factory=list)
    retrieval_status: str = "ok"  # ok | no_results | retrieval_failed
    filtered_knowledge_count: int = 0
    dropped_knowledge_count: int = 0
    coherence_confidence: float = 1.0
    gate_details: dict[str, Any] = field(default_factory=dict)

    @property
    def should_block(self) -> bool:
        return self.outcome in BLOCKING_OUTCOMES

    def as_dict(self) -> dict[str, Any]:
        return {
            "outcome": str(self.outcome),
            "artifact_type": self.artifact_type,
            "reasons": list(self.reasons),
            "retrieval_status": self.retrieval_status,
            "filtered_knowledge_count": self.filtered_knowledge_count,
            "dropped_knowledge_count": self.dropped_knowledge_count,
            "coherence_confidence": self.coherence_confidence,
            "gate_details": dict(self.gate_details),
            "should_block": self.should_block,
        }


def filter_source_relevance(documents: list[Any]) -> tuple[list[Any], list[str]]:
    """§8.4 — drop inapplicable documents from the obligation checklist."""
    kept: list[Any] = []
    dropped: list[str] = []
    for document in documents:
        verdict = getattr(document, "applicability_verdict", "unknown")
        version_conflict = getattr(document, "version_conflict", None)
        # Version mismatch: keep with caveat — not a component irrelevance.
        if verdict in _INAPPLICABLE_VERDICTS and not version_conflict:
            dropped.append(
                f"{getattr(document, 'title', '?')}: applicability={verdict}"
            )
            continue
        kept.append(document)
    return kept, dropped


def infer_artifact_type(
    *,
    pattern: Any,
    knowledge: list[Any],
    episode_summaries: list[dict],
) -> str:
    """§8.2 — route artifact type from source metadata, not title substring matching."""
    from contextedge.quality.seed_data import (
        defect_evidence_types,
        informational_evidence_types,
    )

    evidence_types = {str(getattr(d, "evidence_type", "")).lower() for d in knowledge}

    if evidence_types & defect_evidence_types():
        return ArtifactType.DEFECT_RECORD

    has_procedural_kb = any(
        any(
            getattr(s, "purpose", "context") in _OBLIGATION_PURPOSES
            for s in getattr(d, "sections", [])
        )
        for d in knowledge
    )
    has_episode_steps = any((ep.get("steps") or []) for ep in episode_summaries)

    if has_procedural_kb or has_episode_steps:
        return ArtifactType.PROCEDURAL

    if evidence_types & informational_evidence_types():
        return ArtifactType.INFORMATIONAL

    resolution_steps = getattr(pattern, "resolution_steps", None) or []
    if resolution_steps and not has_procedural_kb:
        return ArtifactType.DIAGNOSTIC

    return ArtifactType.PROCEDURAL


def _token_set(text: str) -> set[str]:
    return set(_TOKEN_RE.findall(text.lower()))


def assess_pattern_coherence(
    *,
    pattern: Any,
    episode_summaries: list[dict],
) -> tuple[float, list[str], ContractOutcome | None]:
    """§8.3 — confidence-aware coherence across episodes."""
    reasons: list[str] = []
    confidence = float(getattr(pattern, "confidence", 0.0) or 0.0)

    root_causes = [
        str(ep.get("root_cause") or "").strip()
        for ep in episode_summaries
        if (ep.get("root_cause") or "").strip()
    ]
    if len(root_causes) >= 2:
        token_sets = [_token_set(rc) for rc in root_causes]
        # Pairwise overlap — low overlap across episodes suggests fragmentation.
        overlaps = []
        for i, left in enumerate(token_sets):
            for right in token_sets[i + 1 :]:
                if not left or not right:
                    overlaps.append(0.0)
                else:
                    overlaps.append(len(left & right) / min(len(left), len(right)))
        min_overlap = min(overlaps) if overlaps else 1.0
        if min_overlap < 0.15:
            reasons.append(
                "Episodes cite distinct root causes with little overlap — "
                "pattern may combine unrelated incidents."
            )
            if confidence >= 0.55:
                return confidence, reasons, ContractOutcome.REQUIRES_SPLIT
            return confidence * 0.5, reasons, ContractOutcome.REQUIRES_EVIDENCE

    titles = [str(ep.get("title") or "") for ep in episode_summaries]
    if len(titles) >= 2:
        title_tokens = [_token_set(t) for t in titles if t.strip()]
        if len(title_tokens) >= 2:
            overlaps = []
            for i, left in enumerate(title_tokens):
                for right in title_tokens[i + 1 :]:
                    if not left or not right:
                        overlaps.append(0.0)
                    else:
                        overlaps.append(len(left & right) / min(len(left), len(right)))
            if overlaps and min(overlaps) < 0.1 and confidence >= 0.6:
                reasons.append(
                    "Episode titles share almost no vocabulary — "
                    "possible over-merge (§4.18)."
                )
                return confidence, reasons, ContractOutcome.REQUIRES_SPLIT

    return confidence, reasons, None


def assess_evidence_sufficiency(
    *,
    contract: QualityContract,
    knowledge: list[Any],
    episode_summaries: list[dict],
    retrieval_status: str,
) -> tuple[list[str], ContractOutcome | None]:
    """§8.4 — distinguish retrieval failure from no applicable knowledge."""
    reasons: list[str] = []

    if retrieval_status == "retrieval_failed":
        reasons.append("Knowledge retrieval failed — cannot ground generation.")
        return reasons, ContractOutcome.REQUIRES_EVIDENCE

    has_obligations = bool(
        contract.required_actions
        or contract.required_validations
        or contract.preconditions
        or contract.rollback_obligations
    )
    has_episode_steps = any((ep.get("steps") or []) for ep in episode_summaries)
    has_episode_outcomes = any(
        (ep.get("root_cause") or ep.get("outcome")) for ep in episode_summaries
    )

    if not knowledge and retrieval_status == "no_results":
        if has_episode_steps or has_episode_outcomes:
            reasons.append(
                "No normative knowledge retrieved; proceeding on observed practice only."
            )
            contract.unresolved_requirements.append(
                "No applicable KB documents — normative obligations unknown."
            )
            return reasons, None
        reasons.append("No knowledge and no usable episode procedure.")
        return reasons, ContractOutcome.REQUIRES_EVIDENCE

    if knowledge and not has_obligations and not has_episode_steps:
        reasons.append(
            "Retrieved knowledge has no structure-derived obligations and "
            "episodes record no observed steps."
        )
        contract.unresolved_requirements.append(
            "Insufficient evidence for an actionable procedure."
        )
        return reasons, ContractOutcome.REQUIRES_EVIDENCE

    return reasons, None


def run_pregeneration_gates(
    contract: QualityContract,
    *,
    pattern: Any,
    knowledge: list[Any],
    episode_summaries: list[dict],
    retrieval_failed: bool = False,
) -> tuple[PreGenerationResult, list[Any]]:
    """Run §8.1–§8.4 gates and return (result, filtered_knowledge)."""
    reasons: list[str] = []
    gate_details: dict[str, Any] = {}

    # §8.1 pipeline readiness
    if not getattr(pattern, "title", "").strip():
        return (
            PreGenerationResult(
                outcome=ContractOutcome.INVALID_INPUT,
                artifact_type=contract.artifact_type,
                reasons=["Pattern has no title."],
                retrieval_status="retrieval_failed" if retrieval_failed else "ok",
            ),
            [],
        )

    if not episode_summaries:
        return (
            PreGenerationResult(
                outcome=ContractOutcome.INVALID_INPUT,
                artifact_type=contract.artifact_type,
                reasons=["Pattern has no episode summaries."],
            ),
            [],
        )

    retrieval_status = "retrieval_failed" if retrieval_failed else "ok"
    if not knowledge and not retrieval_failed:
        retrieval_status = "no_results"

    filtered, dropped = filter_source_relevance(knowledge)
    if dropped:
        gate_details["dropped_knowledge"] = dropped
        reasons.extend(dropped)

    artifact_type = infer_artifact_type(
        pattern=pattern, knowledge=filtered, episode_summaries=episode_summaries
    )
    contract.artifact_type = artifact_type

    coherence_conf, coherence_reasons, coherence_outcome = assess_pattern_coherence(
        pattern=pattern, episode_summaries=episode_summaries
    )
    reasons.extend(coherence_reasons)
    gate_details["coherence"] = {"confidence": coherence_conf, "reasons": coherence_reasons}

    suff_reasons, suff_outcome = assess_evidence_sufficiency(
        contract=contract,
        knowledge=filtered,
        episode_summaries=episode_summaries,
        retrieval_status=retrieval_status,
    )
    reasons.extend(suff_reasons)

    if contract.source_conflicts:
        gate_details["source_conflicts"] = list(contract.source_conflicts)
        return (
            PreGenerationResult(
                outcome=ContractOutcome.REQUIRES_ADJUDICATION,
                artifact_type=artifact_type,
                reasons=reasons + ["Unresolved source conflicts at contract build."],
                retrieval_status=retrieval_status,
                filtered_knowledge_count=len(filtered),
                dropped_knowledge_count=len(dropped),
                coherence_confidence=coherence_conf,
                gate_details=gate_details,
            ),
            filtered,
        )

    # Hard coherence block takes precedence over soft evidence gap.
    if coherence_outcome == ContractOutcome.REQUIRES_SPLIT:
        return (
            PreGenerationResult(
                outcome=ContractOutcome.REQUIRES_SPLIT,
                artifact_type=artifact_type,
                reasons=reasons,
                retrieval_status=retrieval_status,
                filtered_knowledge_count=len(filtered),
                dropped_knowledge_count=len(dropped),
                coherence_confidence=coherence_conf,
                gate_details=gate_details,
            ),
            filtered,
        )

    if suff_outcome == ContractOutcome.REQUIRES_EVIDENCE:
        return (
            PreGenerationResult(
                outcome=ContractOutcome.REQUIRES_EVIDENCE,
                artifact_type=artifact_type,
                reasons=reasons,
                retrieval_status=retrieval_status,
                filtered_knowledge_count=len(filtered),
                dropped_knowledge_count=len(dropped),
                coherence_confidence=coherence_conf,
                gate_details=gate_details,
            ),
            filtered,
        )

    if coherence_outcome == ContractOutcome.REQUIRES_EVIDENCE:
        return (
            PreGenerationResult(
                outcome=ContractOutcome.REQUIRES_EVIDENCE,
                artifact_type=artifact_type,
                reasons=reasons,
                retrieval_status=retrieval_status,
                filtered_knowledge_count=len(filtered),
                dropped_knowledge_count=len(dropped),
                coherence_confidence=coherence_conf,
                gate_details=gate_details,
            ),
            filtered,
        )

    if artifact_type != ArtifactType.PROCEDURAL:
        return (
            PreGenerationResult(
                outcome=ContractOutcome.READY_OTHER_ARTIFACT,
                artifact_type=artifact_type,
                reasons=reasons,
                retrieval_status=retrieval_status,
                filtered_knowledge_count=len(filtered),
                dropped_knowledge_count=len(dropped),
                coherence_confidence=coherence_conf,
                gate_details=gate_details,
            ),
            filtered,
        )

    return (
        PreGenerationResult(
            outcome=ContractOutcome.READY_PROCEDURAL,
            artifact_type=artifact_type,
            reasons=reasons,
            retrieval_status=retrieval_status,
            filtered_knowledge_count=len(filtered),
            dropped_knowledge_count=len(dropped),
            coherence_confidence=coherence_conf,
            gate_details=gate_details,
        ),
        filtered,
    )
