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
# occurrence across the models directory as of migration 0028. Each
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
_EXPECTED_MARKERS: set[tuple[str, str]] = {
    ('decision.py', 'ForeignKey("decisions.id", ondelete="CASCADE"),'),
    ('episode.py', 'ForeignKey("canonical_identities.id", ondelete="CASCADE"),'),
    ('episode.py', 'ForeignKey("evidence_items.id", ondelete="CASCADE"),'),
    ('episode.py',
     'source_evidence_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), '
     'ForeignKey("evidence_items.id", ondelete="CASCADE"), nullable=False)'),
    ('episode.py',
     'target_evidence_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), '
     'ForeignKey("evidence_items.id", ondelete="CASCADE"), nullable=False)'),
    ('evidence.py', 'ForeignKey("tenant_policies.id", ondelete="SET NULL"),'),
    ('evidence.py',
     'evidence_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), '
     'ForeignKey("evidence_items.id", ondelete="CASCADE"), nullable=False)'),
    ('execution.py',
     'UUID(as_uuid=True), ForeignKey("execution_runs.id", ondelete="CASCADE"),'),
    ('execution.py',
     'UUID(as_uuid=True), ForeignKey("execution_step_runs.id", ondelete="CASCADE"),'),
    ('pattern.py', 'ForeignKey("evidence_items.id", ondelete="CASCADE"),'),
    ('pattern.py', 'ForeignKey("playbook_versions.id", ondelete="CASCADE"),'),
    ('playbook.py', 'ForeignKey("evidence_items.id", ondelete="SET NULL"),'),
    ('playbook.py', 'ForeignKey("tenant_policies.id", ondelete="SET NULL"),'),
    ('playbook.py',
     'UniqueConstraint( "playbook_id", "semantic_version", '
     'name="uq_playbook_versions_playbook_semantic_version", ),'),
    ('playbook.py',
     'stable_key: Mapped[str] = mapped_column(String(255), nullable=False, '
     'unique=True, index=True)'),
    ('session.py', 'ForeignKey("resolution_sessions.id", ondelete="CASCADE"),'),
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
