"""Source-derived quality contract (Phase 2, plan §7).

The contract is what generation and post-generation validators judge against.
It is built from pattern fields, episode summaries, and retrieved knowledge —
never from keyword substring matches on arbitrary paragraph text (§4.14).

``quality_contract_hash`` is stamped on the content revision at generation time
so a later assessment can tell whether the playbook was generated against the
contract it claims.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from contextedge.quality.hashing import content_hash

CONTRACT_VERSION = "qc-2026.09.01"


class ContractOutcome(StrEnum):
    """§7.3 — what pre-generation gates conclude."""

    READY_PROCEDURAL = "ready_for_procedural_generation"
    READY_OTHER_ARTIFACT = "ready_for_different_artifact_type"
    REQUIRES_SPLIT = "requires_pattern_split"
    REQUIRES_EVIDENCE = "requires_additional_evidence"
    REQUIRES_ADJUDICATION = "requires_conflict_adjudication"
    INVALID_INPUT = "invalid_input"


class ArtifactType(StrEnum):
    """§8.2 — artifact suitability routing."""

    PROCEDURAL = "procedural"
    DIAGNOSTIC = "diagnostic"
    DEFECT_RECORD = "defect_record"
    INFORMATIONAL = "informational"
    LIMITATION = "limitation"
    PLANNING = "planning"
    CHANGE = "change"
    COMMUNICATION = "communication"


# Outcomes that must not proceed to combined procedural generation.
BLOCKING_OUTCOMES: frozenset[ContractOutcome] = frozenset(
    {
        ContractOutcome.INVALID_INPUT,
        ContractOutcome.REQUIRES_SPLIT,
        ContractOutcome.REQUIRES_ADJUDICATION,
    }
)


@dataclass(frozen=True)
class ClaimProvenance:
    """§7.2 — where a claim came from."""

    source_id: str
    source_type: str  # knowledge | episode | pattern | negative_knowledge
    authority: str  # normative | empirical | negative | inferred
    section_ref: str | None = None
    chunk_kind: str | None = None
    page: int | None = None
    polarity: str = "affirmative"  # affirmative | negative | conditional
    conditionality: str | None = None
    applicability: str = "unknown"
    freshness: str | None = None
    extraction_confidence: float = 1.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "source_type": self.source_type,
            "authority": self.authority,
            "section_ref": self.section_ref,
            "chunk_kind": self.chunk_kind,
            "page": self.page,
            "polarity": self.polarity,
            "conditionality": self.conditionality,
            "applicability": self.applicability,
            "freshness": self.freshness,
            "extraction_confidence": self.extraction_confidence,
        }


@dataclass(frozen=True)
class SourceClaim:
    """One claim with provenance."""

    claim_type: str
    text: str
    provenance: ClaimProvenance
    purpose: str | None = None  # action | prerequisite | validation | rollback | context

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "claim_type": self.claim_type,
            "text": self.text,
            "provenance": self.provenance.as_dict(),
        }
        if self.purpose is not None:
            out["purpose"] = self.purpose
        return out


@dataclass
class QualityContract:
    """§7.1 contract fields — populated from sources, gaps left explicit."""

    contract_version: str = CONTRACT_VERSION
    artifact_type: str = ArtifactType.PROCEDURAL
    audience: str | None = None
    primary_subject: str | None = None
    affected_capability: str | None = None
    affected_component: str | None = None
    failure_mode: str | None = None
    failure_scope: str | None = None
    observed_symptoms: list[str] = field(default_factory=list)
    error_claims: list[str] = field(default_factory=list)
    supported_cause_claims: list[str] = field(default_factory=list)
    uncertainty_notes: list[str] = field(default_factory=list)
    environment_applicability: list[str] = field(default_factory=list)
    version_applicability: list[str] = field(default_factory=list)
    preconditions: list[str] = field(default_factory=list)
    required_actions: list[str] = field(default_factory=list)
    optional_actions: list[str] = field(default_factory=list)
    alternative_branches: list[str] = field(default_factory=list)
    required_validations: list[str] = field(default_factory=list)
    success_criteria: list[str] = field(default_factory=list)
    rollback_obligations: list[str] = field(default_factory=list)
    escalation_criteria: list[str] = field(default_factory=list)
    restricted_actions: list[str] = field(default_factory=list)
    known_failed_actions: list[str] = field(default_factory=list)
    source_conflicts: list[str] = field(default_factory=list)
    unresolved_requirements: list[str] = field(default_factory=list)
    # §4.12 defect-record shape
    defect_identity: str | None = None
    affected_versions: list[str] = field(default_factory=list)
    fixed_in_version: str | None = None
    workaround_validity_window: str | None = None
    # Full claim list for validators (Phase 3)
    claims: list[SourceClaim] = field(default_factory=list)
    pattern_id: str | None = None
    pattern_confidence: float | None = None
    episode_count: int = 0
    knowledge_document_count: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "contract_version": self.contract_version,
            "artifact_type": self.artifact_type,
            "audience": self.audience,
            "primary_subject": self.primary_subject,
            "affected_capability": self.affected_capability,
            "affected_component": self.affected_component,
            "failure_mode": self.failure_mode,
            "failure_scope": self.failure_scope,
            "observed_symptoms": list(self.observed_symptoms),
            "error_claims": list(self.error_claims),
            "supported_cause_claims": list(self.supported_cause_claims),
            "uncertainty_notes": list(self.uncertainty_notes),
            "environment_applicability": list(self.environment_applicability),
            "version_applicability": list(self.version_applicability),
            "preconditions": list(self.preconditions),
            "required_actions": list(self.required_actions),
            "optional_actions": list(self.optional_actions),
            "alternative_branches": list(self.alternative_branches),
            "required_validations": list(self.required_validations),
            "success_criteria": list(self.success_criteria),
            "rollback_obligations": list(self.rollback_obligations),
            "escalation_criteria": list(self.escalation_criteria),
            "restricted_actions": list(self.restricted_actions),
            "known_failed_actions": list(self.known_failed_actions),
            "source_conflicts": list(self.source_conflicts),
            "unresolved_requirements": list(self.unresolved_requirements),
            "defect_identity": self.defect_identity,
            "affected_versions": list(self.affected_versions),
            "fixed_in_version": self.fixed_in_version,
            "workaround_validity_window": self.workaround_validity_window,
            "claims": [claim.as_dict() for claim in self.claims],
            "pattern_id": self.pattern_id,
            "pattern_confidence": self.pattern_confidence,
            "episode_count": self.episode_count,
            "knowledge_document_count": self.knowledge_document_count,
        }


def contract_hash(contract: QualityContract | dict[str, Any]) -> str:
    """Stable digest of the contract payload."""
    payload = contract.as_dict() if isinstance(contract, QualityContract) else contract
    return content_hash(payload)


def format_contract_obligations(contract: QualityContract | dict[str, Any]) -> str:
    """Render contract obligations for the generation prompt.

    Replaces the keyword-derived KB section checklist (§4.14). Only
    structure-derived obligations appear here — nothing inferred from a
    substring like ``required`` in body text.
    """
    data = contract.as_dict() if isinstance(contract, QualityContract) else contract
    lines = [
        "QUALITY CONTRACT (source-derived obligations — do not invent to fill gaps):",
        f"  Artifact type: {data.get('artifact_type', 'procedural')}",
    ]
    if data.get("primary_subject"):
        lines.append(f"  Primary subject: {data['primary_subject']}")
    if data.get("failure_mode"):
        lines.append(f"  Failure mode: {data['failure_mode']}")

    def _section(title: str, items: list[str]) -> None:
        if items:
            lines.append(f"  {title}:")
            for item in items[:12]:
                lines.append(f"    - {item[:400]}")

    _section("Preconditions", data.get("preconditions") or [])
    _section("Required actions", data.get("required_actions") or [])
    _section("Required validations", data.get("required_validations") or [])
    _section("Rollback obligations", data.get("rollback_obligations") or [])
    _section("Known failed actions (do not repeat)", data.get("known_failed_actions") or [])
    _section(
        "Unresolved (abstain — record conflict, do not invent)",
        data.get("unresolved_requirements") or [],
    )
    _section("Source conflicts (reviewer must decide)", data.get("source_conflicts") or [])

    if data.get("artifact_type") == ArtifactType.DEFECT_RECORD:
        if data.get("defect_identity"):
            lines.append(f"  Defect: {data['defect_identity']}")
        if data.get("affected_versions"):
            lines.append(f"  Affected versions: {', '.join(data['affected_versions'][:6])}")
        if data.get("fixed_in_version"):
            lines.append(f"  Fixed in: {data['fixed_in_version']}")

    return "\n".join(lines)
