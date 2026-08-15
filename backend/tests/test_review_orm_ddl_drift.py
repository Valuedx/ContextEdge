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
# occurrence across the models directory as of migration 0029. Each
# entry is (filename, normalised line snippet) produced by
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
_EXPECTED_MARKERS: set[tuple[str, str]] = {
    # trust_profiles is a brand-new table (0062 CREATE TABLE).
    ('trust.py', 'UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"),'),
    ('trust.py',
     'UniqueConstraint( "tenant_id", "agent_ref", "action_type", "resource_class", '
     '"environment", "business_criticality", name="uq_trust_profiles_scope", ),'),
    # verification_assessments + verification_observations are brand-new
    # tables (0061 CREATE TABLE).
    ('verification.py', 'ForeignKey("execution_runs.id", ondelete="CASCADE"),'),
    ('verification.py', 'ForeignKey("verification_assessments.id", ondelete="CASCADE"),'),
    ('verification.py', 'UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"),'),
    ('verification.py',
     'UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False'),
    # execution_attempts is a brand-new table (0060 CREATE TABLE).
    ('attempt.py', 'ForeignKey("execution_step_runs.id", ondelete="CASCADE"),'),
    ('attempt.py',
     'UUID(as_uuid=True), ForeignKey("skills.id", ondelete="SET NULL"), nullable=True'),
    ('attempt.py', 'UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"),'),
    ('attempt.py',
     'UniqueConstraint( "step_run_id", "attempt_number", '
     'name="uq_execution_attempts_step_number" ),'),
    # skills + execution_contracts are brand-new tables (0058 CREATE TABLE).
    # execution_contract_id is RESTRICT on purpose: deleting the contract a
    # live skill runs under would strip its timeout and idempotency
    # guarantees, which is not a thing to allow by cascade.
    ('skill.py', 'ForeignKey("execution_contracts.id", ondelete="RESTRICT"),'),
    ('skill.py', 'UUID(as_uuid=True), ForeignKey("skills.id", ondelete="SET NULL"), nullable=True'),
    ('skill.py', 'UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"),'),
    ('skill.py',
     'UniqueConstraint("tenant_id", "name", name="uq_execution_contracts_tenant_name"),'),
    ('skill.py',
     'UniqueConstraint("tenant_id", "skill_key", "version", name="uq_skills_key_version"),'),
    # policy_checks is a brand-new table (0056 CREATE TABLE), so no ALTER
    # migration is needed for these two — see this test's own guidance.
    ('policy.py', 'ForeignKey("tenant_policies.id", ondelete="SET NULL"),'),
    ('policy.py',
     'UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False'),
    ('action_policy.py', 'ForeignKey("action_policies.id", ondelete="CASCADE"),'),
    ('action_policy.py', 'ForeignKey("decisions.id", ondelete="CASCADE"),'),
    ('action_policy.py', 'ForeignKey("entities.id", ondelete="SET NULL"),'),
    ('action_policy.py',
     'UniqueConstraint( "decision_id", "action_policy_id", '
     'name="uq_decision_action_policies_decision_policy", ),'),
    ('case_outcome.py', 'ForeignKey("case_outcomes.id", ondelete="CASCADE"),'),
    ('case_outcome.py', 'ForeignKey("fix_patterns.id", ondelete="CASCADE"),'),
    ('case_outcome.py',
     'ForeignKey("resolution_sessions.id", ondelete="CASCADE"),'),
    ('case_outcome.py',
     'UniqueConstraint( "case_outcome_id", "fix_pattern_id", "result", '
     'name="uq_case_outcome_fix_patterns_outcome_fix_result", ),'),
    ('claim.py', 'ForeignKey("claims.id", ondelete="CASCADE"),'),
    ('claim.py', 'ForeignKey("decisions.id", ondelete="CASCADE"),'),
    ('claim.py', 'ForeignKey("evidence_items.id", ondelete="CASCADE"),'),
    ('claim.py', 'ForeignKey("resolution_sessions.id", ondelete="CASCADE"),'),
    ('claim.py',
     'UniqueConstraint( "decision_id", "evidence_id", '
     'name="uq_decision_evidence_pair" ),'),
    ('claim.py',
     'UniqueConstraint( "decision_id", "claim_id", "use_type", '
     'name="uq_decision_claims_decision_claim_use", ),'),
    ('claim.py',
     'UniqueConstraint("claim_id", "evidence_id", '
     'name="uq_claim_evidence_pair"),'),
    ('decision.py', 'ForeignKey("decisions.id", ondelete="CASCADE"),'),
    ('entity.py',
     'UniqueConstraint( "tenant_id", "entity_type", "external_system", '
     '"external_id", name="uq_entities_tenant_type_system_external_id", ),'),
    ('episode.py', 'ForeignKey("canonical_identities.id", ondelete="CASCADE"),'),
    ('episode.py', 'ForeignKey("evidence_items.id", ondelete="CASCADE"),'),
    # uq_identity_aliases_tenant_strong — partial unique index created in
    # migration 0033; mirrored into IdentityAlias.__table_args__ so
    # metadata-built schemas enforce strong-alias tenant uniqueness too.
    # case_bridge.py (migration 0038): ticket-number bridging membership
    # model — identifiers, memberships, pending mentions.
    ('case_bridge.py', 'ForeignKey("evidence_items.id", ondelete="CASCADE"),'),
    ('case_bridge.py',
     'UniqueConstraint( "evidence_id", "canonical_case_id", name="uq_evidence_case_membership" ),'),
    ('case_bridge.py',
     'UniqueConstraint( "evidence_id", "normalized_value", name="uq_pending_mention" ),'),
    ('case_bridge.py',
     'UniqueConstraint( "tenant_id", "source_system", "normalized_value", name="uq_case_identifiers_tenant_system_value", ),'),
    # correlation_suggestion.py (migration 0039): gated semantic
    # suggestions — normalized evidence pair, reviewer decision.
    ('correlation_suggestion.py', 'ForeignKey("evidence_items.id", ondelete="CASCADE"),'),
    ('correlation_suggestion.py',
     'UniqueConstraint( "evidence_id_low", "evidence_id_high", name="uq_correlation_suggestion_pair" ),'),
    # fleet_group.py (migration 0048): fleet grouping suggestions.
    ('fleet_group.py', 'ForeignKey("evidence_items.id", ondelete="SET NULL"),'),
    ('fleet_group.py',
     'UniqueConstraint("tenant_id", "change_ref", name="uq_fleet_group_change"),'),
    # fix_cohort.py (migration 0047): per-cohort outcome counters.
    ('fix_cohort.py', 'ForeignKey("fix_patterns.id", ondelete="CASCADE"),'),
    ('fix_cohort.py',
     'UniqueConstraint( "fix_pattern_id", "cohort_type", "cohort_key", name="uq_fix_cohort" ),'),
    # fix_applicability.py (migration 0046): applicability rules.
    ('fix_applicability.py', 'ForeignKey("fix_patterns.id", ondelete="CASCADE"),'),
    # issue_signature.py (migration 0045): problem fingerprints.
    ('issue_signature.py', 'ForeignKey("error_signatures.id", ondelete="SET NULL"),'),
    ('issue_signature.py', 'ForeignKey("episodes.id", ondelete="CASCADE"),'),
    ('issue_signature.py', 'ForeignKey("issue_signatures.id", ondelete="CASCADE"),'),
    # Covered by 0054_error_signature_unique (ALTER on the existing table —
    # nothing wrote to error_signatures before the D1 fingerprinting pass).
    ('error_signature.py',
     'UniqueConstraint("tenant_id", "signature_key", name="uq_error_signature_key"),'),
    ('issue_signature.py',
     'UniqueConstraint("tenant_id", "signature_key", name="uq_issue_signature_key"),'),
    ('issue_signature.py',
     'UniqueConstraint( "episode_id", "issue_signature_id", name="uq_episode_issue_signature" ),'),
    # thread_topic.py (migration 0044): per-thread topic state.
    ('thread_topic.py', 'ForeignKey("threads.id", ondelete="CASCADE"),'),
    ('thread_topic.py', 'UniqueConstraint("thread_id", name="uq_thread_topic"),'),
    # entity_class.py (migration 0042): global class taxonomy.
    ('entity_class.py',
     'UniqueConstraint("canonical_key", name="uq_entity_classes_key"),'),
    ('episode.py', 'unique=True,'),
    # episode_evidence_links (migration 0037): normalized episode↔evidence
    # provenance added in the P0 cluster-materialization work.
    ('episode.py', 'ForeignKey("episodes.id", ondelete="CASCADE"),'),
    ('episode.py',
     'UniqueConstraint("episode_id", "evidence_id", name="uq_episode_evidence"),'),
    # identity_merge_proposals (migration 0052): a brand-new table, so
    # the constraint arrives with its CREATE TABLE rather than an ALTER.
    # It is what makes a reviewer's rejection durable — without it a
    # scheduled reconciliation re-raises every rejected pair forever.
    ('episode.py',
     'UniqueConstraint( "tenant_id", "primary_identity_id", '
     '"duplicate_identity_id", name="uq_identity_merge_proposal_pair", ),'),
    # The CorrelationEdge source/target FK lines were re-wrapped in the
    # 2026-08 lint-debt cleanup: multi-line mapped_column style puts the
    # ForeignKey on its own line, whose fingerprint matches the generic
    # episode.py entry above. No constraint change — pure reformatting.
    ('error_signature.py', 'ForeignKey("entities.id", ondelete="SET NULL"),'),
    ('error_signature.py',
     'ForeignKey("error_signatures.id", ondelete="SET NULL"),'),
    ('error_signature.py', 'ForeignKey("patterns.id", ondelete="SET NULL"),'),
    ('error_signature.py', 'ForeignKey("playbooks.id", ondelete="SET NULL"),'),
    ('evidence.py', 'ForeignKey("tenant_policies.id", ondelete="SET NULL"),'),
    ('evidence.py', 'ForeignKey("evidence_items.id", ondelete="CASCADE"),'),
    # evidence_chunks.evidence_id FK — created in 0030's CREATE TABLE; the
    # mapped_column was reformatted (c02d164) so the marker is the wrapped line.
    ('evidence.py',
     'UUID(as_uuid=True), ForeignKey("evidence_items.id", ondelete="CASCADE"), '
     'nullable=False'),
    ('execution.py', 'ForeignKey("decisions.id", ondelete="SET NULL"),'),
    ('execution.py', 'ForeignKey("resolution_sessions.id", ondelete="SET NULL"),'),
    ('execution.py',
     'UUID(as_uuid=True), ForeignKey("execution_runs.id", ondelete="CASCADE"),'),
    ('execution.py',
     'UUID(as_uuid=True), ForeignKey("execution_step_runs.id", '
     'ondelete="CASCADE"),'),
    ('pattern.py', 'ForeignKey("evidence_items.id", ondelete="CASCADE"),'),
    ('pattern.py', 'ForeignKey("domains.id", ondelete="CASCADE"),'),
    ('pattern.py', 'ForeignKey("playbook_versions.id", ondelete="CASCADE"),'),
    ('pattern.py', 'ForeignKey("tenants.id", ondelete="CASCADE"),'),
    ('pattern.py', 'unique=True,'),
    ('playbook.py', 'ForeignKey("evidence_items.id", ondelete="SET NULL"),'),
    ('playbook.py', 'ForeignKey("tenant_policies.id", ondelete="SET NULL"),'),
    ('playbook.py',
     'UniqueConstraint( "playbook_id", "semantic_version", '
     'name="uq_playbook_versions_playbook_semantic_version", ),'),
    ('playbook.py',
     'UniqueConstraint( "tenant_id", "stable_key", '
     'name="uq_playbooks_tenant_stable_key", ),'),
    ('session.py', 'ForeignKey("decisions.id", ondelete="CASCADE"),'),
    ('session.py', 'ForeignKey("entities.id", ondelete="SET NULL"),'),
    ('session.py', 'ForeignKey("resolution_sessions.id", ondelete="CASCADE"),'),
    ('session.py', 'unique=True,'),
    ('source.py',
     'UUID(as_uuid=True), ForeignKey("tenant_policies.id", ondelete="SET NULL"), '
     'nullable=True'),
    ('tenant.py', 'ForeignKey("tenants.id", ondelete="CASCADE"),'),
    ('tenant.py',
     'slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, '
     'index=True)'),
}


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
