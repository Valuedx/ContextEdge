"""E1-E3: what the agent needs to defend a remediation choice.

Much of this shipped before the roadmap named it; these tests pin the
full chain so a refactor cannot silently break one link of it:

  fix_pattern (success/failure counters) -recommends-> playbook
  case_outcome -validated_fix/invalidated_fix-> fix_pattern (result meta)
  evidence.applicability -> projected constraint facts (this change)
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

from contextedge.graph.agent.hydrators import hydrate_node
from contextedge.graph.agent.profiles import MAF_V1


def test_fix_outcome_chain_is_fully_projectable():
    rels = MAF_V1.relationship_types
    assert "recommends" in rels  # fix_pattern -> playbook (materializer)
    assert "validated_fix" in rels and "invalidated_fix" in rels
    # E3: the failure context rides on the edge, not buried in a body.
    assert "result" in MAF_V1.relationship_metadata["invalidated_fix"]
    assert "result" in MAF_V1.relationship_metadata["validated_fix"]


def test_fix_pattern_facts_carry_the_efficacy_counters():
    fp = SimpleNamespace(
        id=uuid.uuid4(),
        pattern_name="Re-upload web drivers via SysAdmin",
        recommended_fix="Upload matching web drivers through SysAdmin after upgrades.",
        issue_type="agent_workflow_failure",
        failed_step=None,
        preconditions=["agent >= 8.0"],
        risk_level="low",
        approval_required=False,
        success_count=8,
        failure_count=1,
        last_used_at=None,
        confidence=0.82,
        created_at=None,
        updated_at=None,
    )
    node = hydrate_node("fix_pattern", fp)
    assert node.facts["success_count"] == 8
    assert node.facts["failure_count"] == 1
    assert node.confidence == 0.82


def _knowledge_evidence(applicability):
    return SimpleNamespace(
        id=uuid.uuid4(),
        evidence_type="kb_article",
        source_type="local_file",
        evidence_time=None,
        relevance_state="operational",
        relevance_score=0.9,
        title="Web driver management",
        body_summary="Manage web drivers through SysAdmin.",
        applicability=applicability,
        created_at=None,
        updated_at=None,
    )


def test_applicability_constraints_project_as_facts():
    node = hydrate_node(
        "evidence",
        _knowledge_evidence(
            {
                "version_floor": {"automationedge": "8.0"},
                "environments": ["production"],
                "_extractor_scratch": "never project me",
            }
        ),
    )
    assert "version_floor" in node.facts["applicability"]
    assert "_extractor_scratch" not in node.facts["applicability"]


def test_missing_applicability_adds_no_fact():
    node = hydrate_node("evidence", _knowledge_evidence(None))
    assert "applicability" not in node.facts
