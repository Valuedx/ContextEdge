"""Regression guard for the L-01 ORM-DDL drift audit.

The audit found 3 post-0001 FK CASCADE tightenings that had no
corresponding migration; migration ``0028_orm_ddl_drift_alignment``
catches them. This test pins the audit result so a future ORM
tightening that doesn't come with a migration gets caught at CI
time rather than silently diverging on older DBs.

The approach is static: scan every model file for ``ondelete=`` /
``unique=True`` / ``UniqueConstraint(`` usages, and assert that
the set matches the expected snapshot. A new entry → the test
fails, the author has to decide whether (a) to add a migration
and update the snapshot or (b) to convince themselves it's safe.

This is not a perfect guard — it doesn't compare against an actual
DB schema — but it's a cheap canary that catches the common
"added an ondelete, forgot the migration" mistake.
"""

from __future__ import annotations

import pathlib
import re

import pytest


_MODELS_DIR = pathlib.Path(__file__).resolve().parents[1] / "src" / "contextedge" / "models"


def _collect_constraint_markers() -> set[tuple[str, str]]:
    """Return the set of ``(file, marker)`` pairs where a tightening
    constraint appears. ``marker`` is a short fingerprint of the line
    (the first 120 chars after normalisation) so moves don't cause
    false positives."""
    markers: set[tuple[str, str]] = set()
    pattern = re.compile(
        r"(ondelete\s*=\s*[\"'][A-Z_ ]+[\"'])"
        r"|"
        r"(unique\s*=\s*True)"
        r"|"
        r"(UniqueConstraint\([^)]*\))",
    )
    for path in sorted(_MODELS_DIR.glob("*.py")):
        if path.name == "__init__.py":
            continue
        text = path.read_text(encoding="utf-8")
        for match in pattern.finditer(text):
            # Use the match span + a small snippet of context so the
            # marker is informative when a failure prints. We hash-free
            # intentionally — the failure message should be human-
            # readable so the author knows what changed.
            line_start = text.rfind("\n", 0, match.start()) + 1
            line_end = text.find("\n", match.end())
            line = text[line_start:line_end].strip()
            # Normalise whitespace so trivial reformatting doesn't
            # break the snapshot.
            line = re.sub(r"\s+", " ", line)[:160]
            markers.add((path.name, line))
    return markers


# Snapshot of every ``ondelete=`` / ``unique=True`` / ``UniqueConstraint``
# occurrence across the models directory. Last regenerated after
# ``0093_playbook_version_editing`` so copilot / tenant-mixin / nav
# markers that shipped with their CREATE TABLE migrations are included.
# Each entry is (filename, normalised line snippet) produced by
# ``_collect_constraint_markers`` verbatim. Regenerate via:
#
#     python -c "from tests.test_review_orm_ddl_drift import \
#         _collect_constraint_markers; import pprint; \
#         pprint.pp(sorted(_collect_constraint_markers()))"
#
# When this test fails, the diff output tells you exactly which
# markers are new or missing. For each new one: confirm a migration
# ALTERs the constraint on older DBs, then paste the marker here.
#
# Migrations 0029 and 0031 (MAF Context Graph hardening) added several new
# tables (``entities``, ``claims``, ``claim_evidence``,
# ``decision_evidence``, ``action_policies``, ``error_signatures``,
# ``fix_patterns``, ``case_outcomes``, ``case_state_transitions``)
# and several new nullable columns / FKs on existing tables. Every new
# constraint listed below is either part of a CREATE TABLE in 0029
# (no ALTER needed) or covered by an explicit ALTER TABLE block with
# IF EXISTS guards in 0029.
_EXPECTED_MARKERS: set[tuple[str, str]] = {('action_policy.py', 'ForeignKey("action_policies.id", ondelete="CASCADE"),'),
 ('action_policy.py', 'ForeignKey("decisions.id", ondelete="CASCADE"),'),
 ('action_policy.py', 'ForeignKey("entities.id", ondelete="SET NULL"),'),
 ('action_policy.py',
  'UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, '
  'index=True'),
 ('action_policy.py',
  'UniqueConstraint( "decision_id", "action_policy_id", '
  'name="uq_decision_action_policies_decision_policy", ),'),
 ('attempt.py', 'ForeignKey("execution_step_runs.id", ondelete="CASCADE"),'),
 ('attempt.py',
  'UUID(as_uuid=True), ForeignKey("skills.id", ondelete="SET NULL"), nullable=True'),
 ('attempt.py', 'UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"),'),
 ('attempt.py',
  'UniqueConstraint( "step_run_id", "attempt_number", '
  'name="uq_execution_attempts_step_number" ),'),
 ('audit.py', 'ForeignKey("tenants.id", ondelete="CASCADE"),'),
 ('base.py', 'ForeignKey("tenants.id", ondelete="CASCADE"),'),
 ('case_bridge.py', 'ForeignKey("evidence_items.id", ondelete="CASCADE"),'),
 ('case_bridge.py',
  'UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, '
  'index=True'),
 ('case_bridge.py',
  'UniqueConstraint( "evidence_id", "canonical_case_id", '
  'name="uq_evidence_case_membership" ),'),
 ('case_bridge.py',
  'UniqueConstraint( "evidence_id", "normalized_value", name="uq_pending_mention" ),'),
 ('case_bridge.py',
  'UniqueConstraint( "tenant_id", "source_system", "normalized_value", '
  'name="uq_case_identifiers_tenant_system_value", ),'),
 ('case_outcome.py', 'ForeignKey("case_outcomes.id", ondelete="CASCADE"),'),
 ('case_outcome.py', 'ForeignKey("fix_patterns.id", ondelete="CASCADE"),'),
 ('case_outcome.py', 'ForeignKey("resolution_sessions.id", ondelete="CASCADE"),'),
 ('case_outcome.py',
  'UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, '
  'index=True'),
 ('case_outcome.py',
  'UniqueConstraint( "case_outcome_id", "fix_pattern_id", "result", '
  'name="uq_case_outcome_fix_patterns_outcome_fix_result", ),'),
 ('claim.py', 'ForeignKey("claims.id", ondelete="CASCADE"),'),
 ('claim.py', 'ForeignKey("decisions.id", ondelete="CASCADE"),'),
 ('claim.py', 'ForeignKey("evidence_items.id", ondelete="CASCADE"),'),
 ('claim.py', 'ForeignKey("resolution_sessions.id", ondelete="CASCADE"),'),
 ('claim.py',
  'UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, '
  'index=True'),
 ('claim.py',
  'UniqueConstraint( "decision_id", "claim_id", "use_type", '
  'name="uq_decision_claims_decision_claim_use", ),'),
 ('claim.py',
  'UniqueConstraint( "decision_id", "evidence_id", name="uq_decision_evidence_pair" '
  '),'),
 ('claim.py',
  'UniqueConstraint("claim_id", "evidence_id", name="uq_claim_evidence_pair"),'),
 ('copilot.py', 'ForeignKey("copilot_conversations.id", ondelete="CASCADE"),'),
 ('copilot.py', 'ForeignKey("tenants.id", ondelete="CASCADE"),'),
 ('copilot.py', 'ForeignKey("users.id", ondelete="CASCADE"),'),
 ('copilot.py', 'ForeignKey("users.id", ondelete="SET NULL"),'),
 ('copilot.py',
  'UniqueConstraint("conversation_id", "seq", '
  'name="uq_copilot_messages_conversation_seq"),'),
 ('copilot.py',
  'UniqueConstraint("tenant_id", "id", name="uq_copilot_conversations_tenant_id_id"),'),
 ('copilot.py',
  'UniqueConstraint("tenant_id", "id", name="uq_copilot_login_events_tenant_id_id"),'),
 ('copilot.py',
  'UniqueConstraint("tenant_id", "id", name="uq_copilot_messages_tenant_id_id"),'),
 ('copilot.py',
  'UniqueConstraint("tenant_id", "id", name="uq_copilot_usage_events_tenant_id_id"),'),
 ('correlation_suggestion.py', 'ForeignKey("evidence_items.id", ondelete="CASCADE"),'),
 ('correlation_suggestion.py',
  'UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, '
  'index=True'),
 ('correlation_suggestion.py',
  'UniqueConstraint( "evidence_id_low", "evidence_id_high", '
  'name="uq_correlation_suggestion_pair" ),'),
 ('decision.py', 'ForeignKey("decisions.id", ondelete="CASCADE"),'),
 ('decision.py',
  'UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, '
  'index=True,'),
 ('entity.py',
  'UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, '
  'index=True'),
 ('entity.py',
  'UniqueConstraint( "tenant_id", "entity_type", "external_system", "external_id", '
  'name="uq_entities_tenant_type_system_external_id", ),'),
 ('entity_class.py',
  'UniqueConstraint("canonical_key", name="uq_entity_classes_key"),'),
 ('episode.py', 'ForeignKey("canonical_identities.id", ondelete="CASCADE"),'),
 ('episode.py', 'ForeignKey("episodes.id", ondelete="CASCADE"),'),
 ('episode.py', 'ForeignKey("evidence_items.id", ondelete="CASCADE"),'),
 ('episode.py', 'ForeignKey("tenants.id", ondelete="CASCADE"),'),
 ('episode.py',
  'UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False'),
 ('episode.py',
  'UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, '
  'index=True'),
 ('episode.py',
  'UniqueConstraint( "tenant_id", "primary_identity_id", "duplicate_identity_id", '
  'name="uq_identity_merge_proposal_pair", ),'),
 ('episode.py',
  'UniqueConstraint("episode_id", "evidence_id", name="uq_episode_evidence"),'),
 ('episode.py', 'unique=True,'),
 ('error_signature.py', 'ForeignKey("entities.id", ondelete="SET NULL"),'),
 ('error_signature.py', 'ForeignKey("error_signatures.id", ondelete="SET NULL"),'),
 ('error_signature.py', 'ForeignKey("patterns.id", ondelete="SET NULL"),'),
 ('error_signature.py', 'ForeignKey("playbooks.id", ondelete="SET NULL"),'),
 ('error_signature.py',
  'UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, '
  'index=True'),
 ('error_signature.py',
  'UniqueConstraint("tenant_id", "signature_key", name="uq_error_signature_key"),'),
 ('evaluation.py',
  'UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, '
  'index=True'),
 ('evaluation.py',
  'UniqueConstraint( "tenant_id", "id", '
  'name="uq_ranking_calibration_configs_tenant_id_id" ),'),
 ('evaluation.py',
  'UniqueConstraint("tenant_id", "id", name="uq_runtime_match_records_tenant_id_id"),'),
 ('evaluation.py',
  'UniqueConstraint("tenant_id", "match_id", '
  'name="uq_runtime_match_records_tenant_match"),'),
 ('events.py', 'ForeignKey("tenants.id", ondelete="CASCADE"),'),
 ('evidence.py', 'ForeignKey("evidence_items.id", ondelete="CASCADE"),'),
 ('evidence.py', 'ForeignKey("tenant_policies.id", ondelete="SET NULL"),'),
 ('evidence.py',
  'UUID(as_uuid=True), ForeignKey("evidence_items.id", ondelete="CASCADE"), '
  'nullable=False'),
 ('evidence.py',
  'UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, '
  'index=True'),
 ('execution.py', 'ForeignKey("decisions.id", ondelete="SET NULL"),'),
 ('execution.py', 'ForeignKey("execution_runs.id", ondelete="SET NULL"),'),
 ('execution.py', 'ForeignKey("resolution_sessions.id", ondelete="SET NULL"),'),
 ('execution.py',
  'UUID(as_uuid=True), ForeignKey("execution_runs.id", ondelete="CASCADE"),'),
 ('execution.py',
  'UUID(as_uuid=True), ForeignKey("execution_step_runs.id", ondelete="CASCADE"),'),
 ('execution.py',
  'UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, '
  'index=True,'),
 ('fix_applicability.py', 'ForeignKey("fix_patterns.id", ondelete="CASCADE"),'),
 ('fix_applicability.py',
  'UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, '
  'index=True'),
 ('fix_cohort.py', 'ForeignKey("fix_patterns.id", ondelete="CASCADE"),'),
 ('fix_cohort.py',
  'UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, '
  'index=True'),
 ('fix_cohort.py',
  'UniqueConstraint( "tenant_id", "fix_pattern_id", "cohort_type", "cohort_key", '
  'name="uq_fix_cohort", ),'),
 ('fleet_group.py', 'ForeignKey("evidence_items.id", ondelete="SET NULL"),'),
 ('fleet_group.py',
  'UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, '
  'index=True'),
 ('fleet_group.py',
  'UniqueConstraint("tenant_id", "change_ref", name="uq_fleet_group_change"),'),
 ('issue_signature.py', 'ForeignKey("episodes.id", ondelete="CASCADE"),'),
 ('issue_signature.py', 'ForeignKey("error_signatures.id", ondelete="SET NULL"),'),
 ('issue_signature.py', 'ForeignKey("issue_signatures.id", ondelete="CASCADE"),'),
 ('issue_signature.py',
  'UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, '
  'index=True'),
 ('issue_signature.py',
  'UniqueConstraint( "episode_id", "issue_signature_id", '
  'name="uq_episode_issue_signature" ),'),
 ('issue_signature.py',
  'UniqueConstraint("tenant_id", "signature_key", name="uq_issue_signature_key"),'),
 ('knowledge_case.py', 'ForeignKey("knowledge_cases.id", ondelete="CASCADE"),'),
 ('knowledge_case.py',
  'UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, '
  'index=True'),
 ('knowledge_case.py', 'unique=True,'),
 ('knowledge_supersession.py', 'ForeignKey("evidence_items.id", ondelete="CASCADE"),'),
 ('knowledge_supersession.py',
  'UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"),'),
 ('knowledge_supersession.py',
  'UniqueConstraint( "tenant_id", "predecessor_evidence_id", "successor_evidence_id", '
  'name="uq_knowledge_supersession_pair", ),'),
 ('pattern.py', 'ForeignKey("domains.id", ondelete="CASCADE"),'),
 ('pattern.py', 'ForeignKey("evidence_items.id", ondelete="CASCADE"),'),
 ('pattern.py', 'ForeignKey("evidence_items.id", ondelete="SET NULL"),'),
 ('pattern.py', 'ForeignKey("playbook_versions.id", ondelete="CASCADE"),'),
 ('pattern.py', 'ForeignKey("tenants.id", ondelete="CASCADE"),'),
 ('pattern.py',
  'UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, '
  'index=True'),
 ('pattern.py', 'unique=True,'),
 ('playbook.py', 'ForeignKey("evidence_items.id", ondelete="SET NULL"),'),
 ('playbook.py', 'ForeignKey("negative_knowledge_items.id", ondelete="CASCADE"),'),
 ('playbook.py', 'ForeignKey("playbook_versions.id", ondelete="SET NULL"),'),
 ('playbook.py', 'ForeignKey("tenant_policies.id", ondelete="SET NULL"),'),
 ('playbook.py', 'ForeignKey("tenants.id", ondelete="CASCADE"),'),
 ('playbook.py',
  'UUID(as_uuid=True), ForeignKey("playbooks.id", ondelete="CASCADE"), nullable=False'),
 ('playbook.py',
  'UniqueConstraint( "playbook_id", "semantic_version", '
  'name="uq_playbook_versions_playbook_semantic_version", ),'),
 ('playbook.py',
  'UniqueConstraint( "tenant_id", "id", '
  'name="uq_playbook_negative_knowledge_tenant_id_id" ),'),
 ('playbook.py',
  'UniqueConstraint( "tenant_id", "playbook_id", "negative_knowledge_id", '
  'name="uq_pb_nk_tenant_playbook_item", ),'),
 ('playbook.py',
  'UniqueConstraint( "tenant_id", "stable_key", name="uq_playbooks_tenant_stable_key", '
  '),'),
 ('policy.py', 'ForeignKey("tenant_policies.id", ondelete="SET NULL"),'),
 ('policy.py',
  'UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False'),
 ('policy.py',
  'UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, '
  'index=True'),
 ('remediation.py', 'ForeignKey("execution_runs.id", ondelete="CASCADE"),'),
 ('remediation.py', 'ForeignKey("execution_runs.id", ondelete="SET NULL"),'),
 ('remediation.py', 'ForeignKey("resolution_sessions.id", ondelete="SET NULL"),'),
 ('remediation.py', 'ForeignKey("verification_assessments.id", ondelete="SET NULL"),'),
 ('remediation.py',
  'UUID(as_uuid=True), ForeignKey("decisions.id", ondelete="SET NULL"), nullable=True'),
 ('remediation.py',
  'UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"),'),
 ('session.py', 'ForeignKey("decisions.id", ondelete="CASCADE"),'),
 ('session.py', 'ForeignKey("entities.id", ondelete="SET NULL"),'),
 ('session.py', 'ForeignKey("resolution_sessions.id", ondelete="CASCADE"),'),
 ('session.py', 'ForeignKey("tenants.id", ondelete="CASCADE"),'),
 ('session.py', 'unique=True,'),
 ('situation.py',
  'UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, '
  'index=True'),
 ('situation.py', 'unique=True,'),
 ('skill.py', 'ForeignKey("execution_contracts.id", ondelete="RESTRICT"),'),
 ('skill.py',
  'UUID(as_uuid=True), ForeignKey("skills.id", ondelete="SET NULL"), nullable=True'),
 ('skill.py', 'UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"),'),
 ('skill.py',
  'UniqueConstraint("tenant_id", "name", name="uq_execution_contracts_tenant_name"),'),
 ('skill.py',
  'UniqueConstraint("tenant_id", "skill_key", "version", '
  'name="uq_skills_key_version"),'),
 ('source.py',
  'UUID(as_uuid=True), ForeignKey("tenant_policies.id", ondelete="SET NULL"), '
  'nullable=True'),
 ('source.py',
  'UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, '
  'index=True'),
 ('tenant.py', 'ForeignKey("tenants.id", ondelete="CASCADE"),'),
 ('tenant.py',
  'UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, '
  'index=True'),
 ('tenant.py',
  'UniqueConstraint("tenant_id", "username", name="uq_users_tenant_username"),'),
 ('tenant.py',
  '__table_args__ = (UniqueConstraint("role", "href", '
  'name="uq_role_nav_access_role_href"),)'),
 ('tenant.py',
  'slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, '
  'index=True)'),
 ('thread_topic.py', 'ForeignKey("threads.id", ondelete="CASCADE"),'),
 ('thread_topic.py',
  'UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, '
  'index=True'),
 ('thread_topic.py', 'UniqueConstraint("thread_id", name="uq_thread_topic"),'),
 ('trust.py', 'UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"),'),
 ('trust.py',
  'UniqueConstraint( "tenant_id", "agent_ref", "action_type", "resource_class", '
  '"environment", "business_criticality", name="uq_trust_profiles_scope", ),'),
 ('verification.py', 'ForeignKey("execution_runs.id", ondelete="CASCADE"),'),
 ('verification.py', 'ForeignKey("verification_assessments.id", ondelete="CASCADE"),'),
 ('verification.py',
  'UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"),'),
 ('verification.py',
  'UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False')}


def test_no_new_constraint_tightening_without_migration():
    """Fails if a new ``ondelete=`` / ``unique=True`` / ``UniqueConstraint``
    appears in models/*.py without updating this snapshot AND adding
    an ALTER migration to cover older DBs.

    When this fails:
      1. Look at the diff output — the missing entries are new.
      2. For each new entry, decide: is it on a brand-new column/table
         (in which case it came in with a CREATE TABLE migration — no
         ALTER needed), or on an existing one (which needs an
         ALTER TABLE DROP/ADD CONSTRAINT in a new migration)?
      3. Ship the migration if needed, then add the new entries to
         ``_EXPECTED_MARKERS`` above.

    This test does NOT run against a live DB — it's a static guard
    against the "added an ondelete, forgot the migration" class of
    drift. The actual migration set lives under
    ``backend/alembic/versions/``.
    """
    actual = _collect_constraint_markers()
    missing_from_expected = actual - _EXPECTED_MARKERS
    missing_from_actual = _EXPECTED_MARKERS - actual

    if missing_from_expected or missing_from_actual:
        msg_lines = []
        if missing_from_expected:
            msg_lines.append(
                "NEW constraints in models/*.py not captured in the L-01 "
                "snapshot. Before updating the snapshot, confirm an "
                "ALTER migration exists for each (or the column is on "
                "a brand-new table, in which case a CREATE TABLE "
                "migration is fine):"
            )
            for filename, line in sorted(missing_from_expected):
                msg_lines.append(f"  + {filename}: {line}")
        if missing_from_actual:
            msg_lines.append(
                "Snapshot entries that are no longer present (constraint "
                "removed or line changed). Update the snapshot to match:"
            )
            for filename, line in sorted(missing_from_actual):
                msg_lines.append(f"  - {filename}: {line}")
        pytest.fail("\n".join(msg_lines))
