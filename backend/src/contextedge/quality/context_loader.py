"""Load validator context from stored contract, policy pack, and ontology."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.models.playbook import PlaybookVersion
from contextedge.quality.registry import ValidationContext


@dataclass
class AssessmentContextBundle:
    """Everything validators need beyond the content revision."""

    contract: dict[str, Any] | None
    policy_rules: list[dict[str, Any]]
    ontology_terms: list[dict[str, Any]]
    policy_pack_version: str | None
    ontology_version: str | None


def contract_from_evidence_refs(refs: dict[str, Any] | None) -> dict[str, Any] | None:
    """Rebuild the contract dict validators expect from persisted evidence_refs."""
    if not isinstance(refs, dict):
        return None
    qc = refs.get("quality_contract")
    if not isinstance(qc, dict):
        return None
    snapshot = qc.get("snapshot")
    if isinstance(snapshot, dict) and snapshot.get("artifact_type"):
        return dict(snapshot)
    artifact_type = qc.get("artifact_type")
    if not artifact_type:
        return None
    return {
        "artifact_type": artifact_type,
        "outcome": qc.get("outcome"),
        "hash": qc.get("hash"),
    }


def contract_snapshot_from_quality_contract(contract: Any) -> dict[str, Any]:
    """Persist validator-facing contract fields under evidence_refs.quality_contract."""
    if hasattr(contract, "as_dict"):
        data = contract.as_dict()
    elif isinstance(contract, dict):
        data = dict(contract)
    else:
        return {}

    claims = data.get("claims") or []
    slim_claims = []
    for claim in claims[:80]:
        if not isinstance(claim, dict):
            continue
        slim_claims.append(
            {
                "claim_type": claim.get("claim_type"),
                "text": claim.get("text"),
                "purpose": claim.get("purpose"),
            }
        )

    return {
        "artifact_type": data.get("artifact_type"),
        "primary_subject": data.get("primary_subject"),
        "failure_mode": data.get("failure_mode"),
        "affected_component": data.get("affected_component"),
        "affected_capability": data.get("affected_capability"),
        "defect_identity": data.get("defect_identity"),
        "observed_symptoms": list(data.get("observed_symptoms") or []),
        "error_claims": list(data.get("error_claims") or []),
        "supported_cause_claims": list(data.get("supported_cause_claims") or []),
        "preconditions": list(data.get("preconditions") or []),
        "required_actions": list(data.get("required_actions") or []),
        "required_validations": list(data.get("required_validations") or []),
        "rollback_obligations": list(data.get("rollback_obligations") or []),
        "optional_actions": list(data.get("optional_actions") or []),
        "known_failed_actions": list(data.get("known_failed_actions") or []),
        "success_criteria": list(data.get("success_criteria") or []),
        "source_conflicts": list(data.get("source_conflicts") or []),
        "unresolved_requirements": list(data.get("unresolved_requirements") or []),
        "claims": slim_claims,
    }


async def load_assessment_context(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    version: PlaybookVersion | None,
) -> AssessmentContextBundle:
    """Load policy, ontology, and contract for one assessment. Failure-tolerant."""
    from contextedge.services.quality_policy_service import (
        active_ontology_terms,
        active_policy_rules,
    )

    policy_rules: list[dict[str, Any]] = []
    ontology_terms: list[dict[str, Any]] = []
    policy_pack_version: str | None = None
    ontology_version: str | None = None

    try:
        policy_rules, policy_pack_version = await active_policy_rules(db, tenant_id)
    except Exception:  # noqa: BLE001
        policy_rules, policy_pack_version = [], None

    try:
        ontology_terms, ontology_version = await active_ontology_terms(db, tenant_id)
    except Exception:  # noqa: BLE001
        ontology_terms, ontology_version = [], None

    refs = version.evidence_refs if version is not None else None
    contract = contract_from_evidence_refs(refs if isinstance(refs, dict) else None)

    return AssessmentContextBundle(
        contract=contract,
        policy_rules=policy_rules,
        ontology_terms=ontology_terms,
        policy_pack_version=policy_pack_version,
        ontology_version=ontology_version,
    )


def build_validation_context(
    *,
    content: dict[str, Any],
    content_hash: str,
    playbook_id: str,
    tenant_id: str,
    bundle: AssessmentContextBundle,
) -> ValidationContext:
    return ValidationContext(
        content=content,
        content_hash=content_hash,
        playbook_id=playbook_id,
        tenant_id=tenant_id,
        contract=bundle.contract,
        policy_rules=bundle.policy_rules,
        ontology_terms=bundle.ontology_terms,
    )
