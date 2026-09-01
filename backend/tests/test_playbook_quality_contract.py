"""Phase 2: quality contract, section purpose, and pre-generation gates."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from contextedge.quality.contract import (
    ArtifactType,
    ContractOutcome,
    QualityContract,
    contract_hash,
    format_contract_obligations,
)
from contextedge.quality.pregeneration import (
    filter_source_relevance,
    infer_artifact_type,
    run_pregeneration_gates,
)
from contextedge.quality.claim_match import overlap_ratio, tokens
from contextedge.quality.section_purpose import infer_section_purpose
from contextedge.quality.validators.artifact_suitability import validate_artifact_suitability
from contextedge.quality.registry import ValidationContext
from contextedge.quality.states import (
    CATEGORY_TERMINOLOGY_NONCANONICAL,
    DIM_ARTIFACT_SUITABILITY,
    STATE_INCONCLUSIVE,
    STATE_PASS,
)
from contextedge.services.knowledge_retrieval_service import KnowledgeDocument, KnowledgeSection
from contextedge.services.quality_contract_service import prepare_playbook_generation


def _pattern(**kwargs):
    return SimpleNamespace(
        id=uuid.uuid4(),
        title=kwargs.get("title", "Agent fails to start"),
        description=kwargs.get("description", "Startup error after upgrade"),
        confidence=kwargs.get("confidence", 0.7),
        observed_errors=kwargs.get("observed_errors"),
        root_causes=kwargs.get("root_causes"),
        resolution_steps=kwargs.get("resolution_steps"),
        core_entities=kwargs.get("core_entities"),
    )


def _doc(
    *,
    verdict: str = "applies",
    version_conflict: tuple[str, str] | None = None,
    sections: list[KnowledgeSection] | None = None,
    evidence_type: str = "kb_article",
):
    return KnowledgeDocument(
        evidence_id=uuid.uuid4(),
        title="Restart Agent",
        evidence_type=evidence_type,
        sections=sections or [],
        applicability_verdict=verdict,
        version_conflict=version_conflict,
    )


class TestClaimMatch:
    def test_tokens_keep_short_operational_words(self):
        toks = tokens("the agent service restart")
        assert "the" in toks
        assert "agent" in toks

    def test_overlap_without_stopword_filtering(self):
        assert overlap_ratio("restart agent service", "agent service restart") == 1.0


class TestSectionPurpose:
    def test_body_text_required_does_not_become_prerequisite(self):
        """§4.14 — the word 'required' in body text must not create obligations."""
        purpose = infer_section_purpose(
            text="This field is required for all deployments in production.",
            parent_section="Overview",
            chunk_kind="heading_section",
        )
        assert purpose == "context"

    def test_procedure_step_chunk_is_action(self):
        assert (
            infer_section_purpose(
                text="Run systemctl restart ae-agent",
                parent_section="References",
                chunk_kind="procedure_step",
            )
            == "action"
        )

    def test_resolution_heading_is_action(self):
        assert (
            infer_section_purpose(
                text="Stop the service, delete the JAR, restart.",
                parent_section="Resolution",
                chunk_kind="heading_section",
            )
            == "action"
        )

    def test_rollback_heading(self):
        assert (
            infer_section_purpose(
                text="Restore the backup configuration.",
                parent_section="Rollback procedure",
                chunk_kind="heading_section",
            )
            == "rollback"
        )


class TestSourceRelevance:
    def test_component_mismatch_dropped(self):
        doc = _doc(verdict="mismatch")
        kept, dropped = filter_source_relevance([doc])
        assert kept == []
        assert dropped

    def test_version_mismatch_kept(self):
        doc = _doc(verdict="mismatch", version_conflict=("8.1.0", "8.0.0"))
        kept, _ = filter_source_relevance([doc])
        assert len(kept) == 1


class TestQualityContract:
    def test_builds_obligations_from_structure_not_keywords(self):
        doc = _doc(
            sections=[
                KnowledgeSection(
                    text="The administrator must approve the change.",
                    section_ref="Overview",
                    purpose="context",
                ),
                KnowledgeSection(
                    text="Restart the Widget Service.",
                    section_ref="Resolution",
                    purpose="action",
                    chunk_kind="procedure_step",
                ),
            ]
        )
        prep = prepare_playbook_generation(
            pattern=_pattern(),
            episode_summaries=[{"id": "e1", "title": "Agent down", "steps": ["Restart agent"]}],
            knowledge=[doc],
        )
        assert "Restart the Widget Service." in prep.contract.required_actions
        assert not any("administrator must approve" in a for a in prep.contract.required_actions)
        assert prep.contract_hash == contract_hash(prep.contract)
        assert len(prep.contract_hash) == 64

    def test_contract_prompt_lists_obligations(self):
        contract = QualityContract(
            artifact_type=ArtifactType.PROCEDURAL,
            required_actions=["Restart the agent"],
            unresolved_requirements=["No KB for rollback"],
        )
        block = format_contract_obligations(contract)
        assert "Restart the agent" in block
        assert "abstain" in block.lower()


class TestPreGenerationGates:
    def test_ready_for_procedural_generation(self):
        doc = _doc(
            sections=[
                KnowledgeSection(
                    text="Restart service",
                    section_ref="Resolution",
                    purpose="action",
                )
            ]
        )
        prep = prepare_playbook_generation(
            pattern=_pattern(),
            episode_summaries=[{"id": "e1", "root_cause": "Stale lock file", "steps": ["Delete lock"]}],
            knowledge=[doc],
        )
        assert prep.gate.outcome == ContractOutcome.READY_PROCEDURAL
        assert not prep.should_block

    def test_requires_adjudication_on_conflicting_root_causes(self):
        prep = prepare_playbook_generation(
            pattern=_pattern(confidence=0.8),
            episode_summaries=[
                {"id": "e1", "root_cause": "Memory leak in plugin A", "title": "Plugin A OOM"},
                {"id": "e2", "root_cause": "Database connection timeout", "title": "DB timeout"},
            ],
            knowledge=[],
        )
        assert prep.gate.outcome == ContractOutcome.REQUIRES_ADJUDICATION
        assert prep.should_block

    def test_requires_split_on_unrelated_episode_titles(self):
        prep = prepare_playbook_generation(
            pattern=_pattern(confidence=0.75),
            episode_summaries=[
                {"id": "e1", "title": "VPN gateway session limit exceeded"},
                {"id": "e2", "title": "ActiveMQ broker disk full alert"},
            ],
            knowledge=[],
        )
        assert prep.gate.outcome == ContractOutcome.REQUIRES_SPLIT
        assert prep.should_block

    def test_retrieval_failure_is_explicit(self):
        prep = prepare_playbook_generation(
            pattern=_pattern(),
            episode_summaries=[{"id": "e1", "title": "Issue"}],
            knowledge=[],
            retrieval_failed=True,
        )
        assert prep.gate.outcome == ContractOutcome.REQUIRES_EVIDENCE
        assert prep.gate.retrieval_status == "retrieval_failed"

    def test_defect_record_routing(self):
        artifact = infer_artifact_type(
            pattern=_pattern(title="Known defect in agent startup"),
            knowledge=[_doc(evidence_type="release_notes")],
            episode_summaries=[],
        )
        assert artifact == ArtifactType.DEFECT_RECORD


class TestArtifactSuitabilityValidator:
    def test_passes_when_contract_and_steps_align(self):
        ctx = ValidationContext(
            content={"steps": [{"step_id": "s1", "text": "Restart"}]},
            content_hash="abc",
            playbook_id="p1",
            tenant_id="t1",
            contract={"artifact_type": "procedural"},
        )
        result = validate_artifact_suitability(ctx)
        assert result.dimension_states[DIM_ARTIFACT_SUITABILITY] == STATE_PASS

    def test_inconclusive_without_contract(self):
        ctx = ValidationContext(
            content={"steps": []},
            content_hash="abc",
            playbook_id="p1",
            tenant_id="t1",
        )
        result = validate_artifact_suitability(ctx)
        assert result.dimension_states[DIM_ARTIFACT_SUITABILITY] == STATE_INCONCLUSIVE


def test_pregeneration_gate_blocks_on_source_conflicts():
    contract = QualityContract(source_conflicts=["Episodes disagree on cause"])
    gate, _ = run_pregeneration_gates(
        contract,
        pattern=_pattern(),
        knowledge=[],
        episode_summaries=[{"id": "e1", "root_cause": "A"}],
    )
    assert gate.outcome == ContractOutcome.REQUIRES_ADJUDICATION
    assert gate.should_block


class TestMatchingPrecision:
    def test_short_alias_does_not_match_inside_unrelated_words(self):
        from contextedge.quality.claim_match import contains_phrase, ontology_terms_present
        from contextedge.quality.validators.subject import validate as validate_subject

        ont = [
            {
                "canonical_term": "Design Console",
                "term_kind": "component",
                "aliases": ["DC", "Studio"],
            }
        ]
        assert not contains_phrase("Follow these records to restart the service", "DC")
        assert "Design Console" in ontology_terms_present(
            "Open Design Console and restart the service", ont
        )

        ctx = ValidationContext(
            content={
                "title": "Service restart",
                "description": "",
                "steps": [{"text": "Follow these records to restart the service"}],
            },
            content_hash="abc",
            playbook_id="p1",
            tenant_id="t1",
            contract={},
            ontology_terms=ont,
        )
        result = validate_subject(ctx)
        alias_findings = [
            f for f in result.findings if f.category == CATEGORY_TERMINOLOGY_NONCANONICAL
        ]
        assert alias_findings == []

    def test_multi_word_ontology_term_detected_in_steps(self):
        from contextedge.quality.claim_match import ontology_terms_present

        ont = [{"canonical_term": "Widget Service", "aliases": ["WS"]}]
        found = ontology_terms_present(
            "Restart the Widget Service on host-prod-01", ont
        )
        assert "Widget Service" in found

    def test_short_policy_rules_require_all_tokens(self):
        from contextedge.quality.policy_match import rule_matches_action

        config_rule = {"normalized_action": "change config file"}
        assert not rule_matches_action(config_rule, "Change the log file on server")
        assert rule_matches_action(config_rule, "Change the config file on server")

        reregister_rule = {"normalized_action": "re-register service"}
        assert not rule_matches_action(reregister_rule, "Re-running the service workflow")
        assert rule_matches_action(reregister_rule, "Re-register the service now")
