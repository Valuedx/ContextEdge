"""Build the source-derived quality contract and run pre-generation gates."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from contextedge.quality.contract import (
    ArtifactType,
    ClaimProvenance,
    QualityContract,
    SourceClaim,
    contract_hash,
    format_contract_obligations,
)
from contextedge.quality.hashing import content_hash
from contextedge.quality.pregeneration import PreGenerationResult, run_pregeneration_gates

_OBLIGATION_PURPOSES = frozenset({"action", "prerequisite", "validation", "rollback"})


@dataclass
class GenerationPreparation:
    """Everything a generation path needs after pre-generation gates."""

    contract: QualityContract
    gate: PreGenerationResult
    filtered_knowledge: list[Any]
    contract_hash: str
    source_snapshot_hash: str
    contract_prompt_block: str

    @property
    def should_block(self) -> bool:
        return self.gate.should_block

    def evidence_refs_quality(self) -> dict[str, Any]:
        """Compact blob stored under evidence_refs.quality_contract."""
        return {
            "hash": self.contract_hash,
            "source_snapshot_hash": self.source_snapshot_hash,
            "gate": self.gate.as_dict(),
            "artifact_type": self.contract.artifact_type,
            "outcome": str(self.gate.outcome),
        }


def _claim_from_knowledge_section(document: Any, section: Any) -> SourceClaim:
    purpose = getattr(section, "purpose", "context")
    return SourceClaim(
        claim_type="normative_procedure",
        text=(getattr(section, "text", "") or "").strip()[:1200],
        purpose=purpose,
        provenance=ClaimProvenance(
            source_id=str(getattr(document, "evidence_id", "")),
            source_type="knowledge",
            authority="normative",
            section_ref=getattr(section, "section_ref", None),
            chunk_kind=getattr(section, "chunk_kind", None),
            page=getattr(section, "page", None),
            applicability=getattr(document, "applicability_verdict", "unknown"),
            extraction_confidence=0.9 if not getattr(section, "model_derived", False) else 0.6,
        ),
    )


def _append_obligations(contract: QualityContract, claim: SourceClaim) -> None:
    purpose = claim.purpose or "context"
    text = claim.text
    if not text or purpose not in _OBLIGATION_PURPOSES:
        return
    if purpose == "action":
        contract.required_actions.append(text)
    elif purpose == "prerequisite":
        contract.preconditions.append(text)
    elif purpose == "validation":
        contract.required_validations.append(text)
    elif purpose == "rollback":
        contract.rollback_obligations.append(text)


def build_quality_contract(
    *,
    pattern: Any,
    episode_summaries: list[dict],
    knowledge: list[Any],
    negative_knowledge: list[str] | None = None,
) -> QualityContract:
    """Assemble §7.1 from pattern, episodes, and retrieved knowledge."""
    contract = QualityContract(
        primary_subject=getattr(pattern, "title", None),
        failure_mode=getattr(pattern, "description", None),
        pattern_id=str(getattr(pattern, "id", "")) or None,
        pattern_confidence=float(getattr(pattern, "confidence", 0.0) or 0.0),
        episode_count=len(episode_summaries),
        knowledge_document_count=len(knowledge),
    )

    for entity in getattr(pattern, "core_entities", None) or []:
        contract.affected_component = contract.affected_component or str(entity)

    for symptom in getattr(pattern, "observed_errors", None) or []:
        contract.observed_symptoms.append(str(symptom))
        contract.error_claims.append(str(symptom))

    for cause in getattr(pattern, "root_causes", None) or []:
        contract.supported_cause_claims.append(str(cause))
        contract.claims.append(
            SourceClaim(
                claim_type="inferred_cause",
                text=str(cause),
                provenance=ClaimProvenance(
                    source_id=str(getattr(pattern, "id", "")),
                    source_type="pattern",
                    authority="inferred",
                    extraction_confidence=float(getattr(pattern, "confidence", 0.0) or 0.0),
                ),
            )
        )

    for step in getattr(pattern, "resolution_steps", None) or []:
        contract.optional_actions.append(str(step))

    root_causes: list[str] = []
    for ep in episode_summaries:
        ep_id = str(ep.get("id") or "")
        if ep.get("title"):
            contract.observed_symptoms.append(str(ep["title"]))
        if ep.get("root_cause"):
            rc = str(ep["root_cause"])
            root_causes.append(rc)
            contract.supported_cause_claims.append(rc)
            contract.claims.append(
                SourceClaim(
                    claim_type="empirical_cause",
                    text=rc,
                    provenance=ClaimProvenance(
                        source_id=ep_id,
                        source_type="episode",
                        authority="empirical",
                        extraction_confidence=0.85,
                    ),
                )
            )
        if ep.get("outcome"):
            contract.success_criteria.append(str(ep["outcome"]))
        for step in ep.get("steps") or []:
            text = step if isinstance(step, str) else str(step.get("text") or step)
            if text.strip():
                claim = SourceClaim(
                    claim_type="empirical_action",
                    text=text.strip()[:800],
                    purpose="action",
                    provenance=ClaimProvenance(
                        source_id=ep_id,
                        source_type="episode",
                        authority="empirical",
                        extraction_confidence=0.8,
                    ),
                )
                contract.claims.append(claim)
                contract.optional_actions.append(text.strip()[:800])

    if len(set(root_causes)) > 1:
        contract.source_conflicts.append(
            "Episodes report different root causes: "
            + "; ".join(sorted(set(root_causes))[:4])
        )

    for document in knowledge:
        for section in getattr(document, "sections", []) or []:
            claim = _claim_from_knowledge_section(document, section)
            contract.claims.append(claim)
            _append_obligations(contract, claim)

        product_version = getattr(document, "product_version", None)
        if product_version:
            contract.version_applicability.append(str(product_version))
        for note in getattr(document, "applicability_notes", []) or []:
            contract.environment_applicability.append(str(note))

        evidence_type = str(getattr(document, "evidence_type", "")).lower()
        title = str(getattr(document, "title", ""))
        if evidence_type in {"release_notes", "defect", "known_issue"}:
            contract.artifact_type = ArtifactType.DEFECT_RECORD
            contract.defect_identity = contract.defect_identity or title
        if product_version and contract.artifact_type == ArtifactType.DEFECT_RECORD:
            contract.affected_versions.append(str(product_version))

    for neg in negative_knowledge or []:
        text = str(neg).strip()
        if not text:
            continue
        contract.known_failed_actions.append(text)
        contract.claims.append(
            SourceClaim(
                claim_type="negative_knowledge",
                text=text,
                provenance=ClaimProvenance(
                    source_id="negative_knowledge",
                    source_type="negative_knowledge",
                    authority="negative",
                    polarity="negative",
                    extraction_confidence=0.95,
                ),
            )
        )

    return contract


def build_source_snapshot(
    *,
    pattern: Any,
    episode_summaries: list[dict],
    knowledge: list[Any],
    negative_knowledge: list[str] | None = None,
) -> dict[str, Any]:
    """Hashable snapshot of generation inputs for staleness detection."""
    return {
        "pattern_id": str(getattr(pattern, "id", "")),
        "pattern_confidence": float(getattr(pattern, "confidence", 0.0) or 0.0),
        "episode_ids": sorted(str(ep.get("id") or "") for ep in episode_summaries),
        "knowledge_ids": sorted(str(getattr(d, "evidence_id", "")) for d in knowledge),
        "negative_knowledge": list(negative_knowledge or [])[:20],
    }


def prepare_playbook_generation(
    *,
    pattern: Any,
    episode_summaries: list[dict],
    knowledge: list[Any],
    negative_knowledge: list[str] | None = None,
    retrieval_failed: bool = False,
) -> GenerationPreparation:
    """Build contract, run gates, return filtered knowledge and hashes."""
    contract = build_quality_contract(
        pattern=pattern,
        episode_summaries=episode_summaries,
        knowledge=knowledge,
        negative_knowledge=negative_knowledge,
    )
    gate, filtered = run_pregeneration_gates(
        contract,
        pattern=pattern,
        knowledge=knowledge,
        episode_summaries=episode_summaries,
        retrieval_failed=retrieval_failed,
    )
    snapshot = build_source_snapshot(
        pattern=pattern,
        episode_summaries=episode_summaries,
        knowledge=filtered,
        negative_knowledge=negative_knowledge,
    )
    digest = contract_hash(contract)
    snapshot_digest = content_hash(snapshot)
    return GenerationPreparation(
        contract=contract,
        gate=gate,
        filtered_knowledge=filtered,
        contract_hash=digest,
        source_snapshot_hash=snapshot_digest,
        contract_prompt_block=format_contract_obligations(contract),
    )
