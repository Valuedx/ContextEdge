"""Tests for assessment context loading from evidence_refs and policy DB."""

from __future__ import annotations

from contextedge.quality.context_loader import (
    AssessmentContextBundle,
    build_validation_context,
    contract_from_evidence_refs,
    contract_snapshot_from_quality_contract,
)
from contextedge.quality.contract import ArtifactType, QualityContract


class TestContractFromEvidenceRefs:
    def test_full_snapshot_roundtrip(self):
        contract = QualityContract(
            artifact_type=ArtifactType.PROCEDURAL,
            primary_subject="agent restart",
            required_actions=["Restart the agent service"],
            observed_symptoms=["Agent offline"],
        )
        snapshot = contract_snapshot_from_quality_contract(contract)
        refs = {"quality_contract": {"hash": "abc123", "snapshot": snapshot}}
        loaded = contract_from_evidence_refs(refs)
        assert loaded is not None
        assert loaded["artifact_type"] == ArtifactType.PROCEDURAL
        assert loaded["primary_subject"] == "agent restart"
        assert "Restart the agent service" in loaded["required_actions"]

    def test_legacy_minimal_blob(self):
        refs = {
            "quality_contract": {
                "hash": "legacy",
                "artifact_type": ArtifactType.DEFECT_RECORD,
                "outcome": "proceed",
            }
        }
        loaded = contract_from_evidence_refs(refs)
        assert loaded == {
            "artifact_type": ArtifactType.DEFECT_RECORD,
            "outcome": "proceed",
            "hash": "legacy",
        }

    def test_missing_contract_returns_none(self):
        assert contract_from_evidence_refs(None) is None
        assert contract_from_evidence_refs({}) is None


class TestBuildValidationContext:
    def test_bundle_fields_reach_context(self):
        bundle = AssessmentContextBundle(
            contract={"artifact_type": "procedural", "required_actions": ["x"]},
            policy_rules=[{"normalized_action": "delete jar", "decision": "forbidden"}],
            ontology_terms=[{"canonical_term": "AutomationEdge Agent", "aliases": []}],
            policy_pack_version="1.0.0",
            ontology_version="1.0.0",
        )
        ctx = build_validation_context(
            content={"title": "t", "steps": []},
            content_hash="hash1",
            playbook_id="pb1",
            tenant_id="tn1",
            bundle=bundle,
        )
        assert ctx.contract["required_actions"] == ["x"]
        assert len(ctx.policy_rules) == 1
        assert ctx.ontology_terms[0]["canonical_term"] == "AutomationEdge Agent"
