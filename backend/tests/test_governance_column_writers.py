"""Guard: every mapped column either has a writer, or an owner (F1).

Migration ``0029_ae_ops_concept_alignment`` provisioned a governance spine
schema-first and said so — ``codewiki/17`` lists "service-code population of
new fields" as out of scope. The columns then sat unwritten long enough that
an external schema review scored them as shipped capability, because a column
that exists looks exactly like a column that works.

This test makes that state impossible to hold silently. It scans every
``mapped_column`` in ``models/`` and looks for a writer anywhere else under
``src/contextedge``. Anything with no writer must appear in ``EXPECTED_UNWRITTEN``
with an **owner** and a **reason** — "provisioned, unwired, owner: F8" is a
truthful state; silence is not.

The assertion is set equality, in both directions:

- A newly unwritten column fails → wire it, or claim it in the register.
- A column that gained a writer fails → remove it from the register, so the
  register never claims a gap that has since been closed.

**Known limitation: writer detection is by column NAME, not by model.** Two
models sharing a column name share a verdict — writing
``ExecutionStepRun.action_name`` makes ``ActionPolicy.action_name`` look
written, which is why the latter is absent from the register below even though
``action_policies`` has no writer at all. Model-aware detection would need real
dataflow analysis; the register carries the shadowed cases in its reasons
instead. Same for ``ApprovalRequest.approver_role`` vs
``ActionPolicy.required_approver_roles``.

Dynamic writes count: a column assigned through a ``setattr(obj, field, value)``
update loop is detected when its name appears as a string literal in a file that
also contains ``setattr(`` — that is how the CMDB trait refresh and most admin
PATCH routes write.
"""

from __future__ import annotations

import functools
import pathlib
import re

_SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "contextedge"
_MODELS = _SRC / "models"

# (model file, column) -> (owner, reason). Owner is an Epic F item where the
# column is scheduled, or a category when no work is pending.
EXPECTED_UNWRITTEN: dict[tuple[str, str], tuple[str, str]] = {
    # --- action_policies: the table has no writer and no CRUD API. Read only
    # by the agent projection; the executor gates on TenantPolicy instead.
    **{
        ("action_policy.py", col): ("F3", "action_policies has no writer and no CRUD API")
        for col in (
            "action_policy_id",
            "allowed_execution_mode",
            "business_unit",
            "conflict_resolution",
            "data_domain",
            "policy_name",
            "policy_result",
            "policy_result_snapshot",
            "policy_scope",
            "required_approver_roles",
            "restrictions",
            "workflow_entity_id",
        )
    },
    # --- decisions
    ("decision.py", "policy_result"): (
        "F3",
        "the executor has no action-policy verdict to record until F3 builds the engine",
    ),
    ("decision.py", "decision_summary"): (
        "F5",
        "retire candidate: rationale_summary already carries the canonical summary",
    ),
    # --- execution: approval integrity and idempotency
    **{
        ("execution.py", col): ("F7", reason)
        for col, reason in (
            ("recommended_by", "the recommender↔approver SoD axis needs a decision anchor"),
            ("sod_check_status", "no SoD check runs; only the initiator↔approver ban is enforced"),
            ("sod_violation_reason", "written by the SoD check that does not exist yet"),
            ("decision_trace_id", "approvals are not raised from a decision today"),
            ("approval_channel", "approvals only flow through the API; no channel to record"),
            ("approval_note", "the approval surface that would carry it is not built"),
        )
    },
    **{
        ("execution.py", col): ("F8", reason)
        for col, reason in (
            (
                "idempotency_key",
                "uq_execution_step_runs_idempotency_key guards an always-NULL column",
            ),
            ("duplicate_check_status", "there is no duplicate check to record the result of"),
        )
    },
    # --- claims: created, never validated; both link tables have no constructor
    **{
        ("claim.py", col): ("dormant-feature", reason)
        for col, reason in (
            ("claim_id", "decision_claims has no constructor: no decision cites a claim"),
            ("use_type", "decision_claims has no constructor"),
            ("support_type", "claim_evidence has no constructor either"),
            ("validated_by", "claims stay 'unverified'; nothing validates one"),
            ("validated_at", "claims stay 'unverified'; nothing validates one"),
            ("validation_note", "claims stay 'unverified'; nothing validates one"),
            ("superseded_by_claim_id", "no supersession path; the chain is never written"),
        )
    },
    # --- fix patterns: FixPattern has no constructor ANYWHERE. fix_patterns is
    # never populated, so the B4 applicability join, the B5 cohort counters and
    # the execution-verification write-back are all dormant in practice.
    **{
        ("error_signature.py", col): ("dormant-feature", "FixPattern has no constructor anywhere")
        for col in (
            "pattern_name",
            "issue_type",
            "workflow_entity_id",
            "error_signature_id",
            "failed_step",
            "recommended_fix",
            "recommended_playbook_id",
            "source_case_ids",
        )
    },
    **{
        ("error_signature.py", col): (
            "dormant-feature",
            "the fingerprinting pass writes the key and message only",
        )
        for col in ("usual_causes", "recommended_actions", "risk_notes", "last_used_at")
    },
    ("issue_signature.py", "error_signature_id"): (
        "dormant-feature",
        "the issue-signature ↔ error-signature bridge is never set",
    ),
    # --- case spine (0029): resolution_sessions' structured columns
    **{
        ("session.py", col): ("F3", "case-spine columns the policy scope will key on")
        for col in (
            "agent_entity_id",
            "case_number",
            "case_type",
            "issue_type",
            "request_entity_id",
            "severity",
            "user_entity_id",
            "workflow_entity_id",
        )
    },
    **{
        ("session.py", col): ("F5", "tool I/O references belong with generation provenance")
        for col in ("tool_input_ref", "tool_output_ref")
    },
    # --- evidence lineage
    ("evidence.py", "collected_by"): ("F5", "the collecting agent is not recorded on the row"),
    ("evidence.py", "redaction_status"): (
        "dormant-feature",
        "redaction runs but does not stamp the per-row marker",
    ),
    ("entity.py", "business_unit"): ("F3", "tenant separation axis; no ingest path sets it"),
    ("entity.py", "data_domain"): ("F3", "tenant separation axis; no ingest path sets it"),
    # --- db-generated / default-only / migration-seeded
    ("events.py", "recorded_at"): ("db-generated", "server_default now()"),
    ("evidence.py", "stored_at"): ("db-generated", "server_default now()"),
    **{
        ("entity_class.py", col): ("migration-seeded", "the class tree is seeded by 0042")
        for col in ("attributes_schema", "class_family", "parent_class_id")
    },
    ("case_bridge.py", "identifier_type"): ("default-only", "'number' is the only kind registered"),
    ("case_bridge.py", "is_authoritative"): (
        "default-only",
        "nothing registers a non-authoritative identifier",
    ),
    ("case_outcome.py", "should_create_or_update_pattern"): (
        "default-only",
        "no closure opts out of pattern learning yet",
    ),
    ("playbook.py", "expiry_at"): (
        "default-only",
        "start_execution enforces it; no surface sets one",
    ),
    **{
        ("source.py", col): ("F5", "source admin fields have no console; E5 CRUD remains open")
        for col in (
            "approved_for_backfill",
            "backfill_window_days",
            "residency_region",
            "steady_state_sync_enabled",
        )
    },
    **{
        ("tenant.py", col): ("F5", "tenant admin CRUD remains open (E5)")
        for col in ("retention_defaults", "sso_config", "sso_provider")
    },
}

_DECL = re.compile(r"^\s{4}(\w+)\s*(?::\s*Mapped\[[^\]]*\]\s*)?=\s*(.*)$")


def _declared_columns() -> dict[str, set[str]]:
    """``{model file: {column names}}`` for real mapped columns.

    Relationships and ``Computed`` columns are excluded: neither is ever
    assigned by application code, so requiring a writer would be noise.
    """
    declared: dict[str, set[str]] = {}
    for path in sorted(_MODELS.glob("*.py")):
        if path.name in {"__init__.py", "base.py"}:
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        for idx, line in enumerate(lines):
            match = _DECL.match(line)
            if match is None:
                continue
            name, rhs = match.group(1), match.group(2)
            if name.startswith("_"):
                continue
            window = " ".join(lines[idx : idx + 3])
            if "mapped_column(" not in rhs and "mapped_column(" not in window:
                continue
            if "Computed(" in window:
                continue
            declared.setdefault(path.name, set()).add(name)
    return declared


def _writer_corpus() -> tuple[str, str]:
    """Everything under ``src/contextedge`` except ``models/``.

    Returned twice: the whole corpus (direct assignment) and the subset of
    files containing ``setattr(`` (dynamic assignment through a field list).
    """
    direct: list[str] = []
    dynamic: list[str] = []
    for path in _SRC.rglob("*.py"):
        if "models" in path.parts or "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        direct.append(text)
        if "setattr(" in text:
            dynamic.append(text)
    return "\n".join(direct), "\n".join(dynamic)


@functools.lru_cache(maxsize=1)
def _unwritten_columns() -> frozenset[tuple[str, str]]:
    """Cached: three tests share one scan of the tree."""
    direct, dynamic = _writer_corpus()
    unwritten: set[tuple[str, str]] = set()
    for fname, columns in _declared_columns().items():
        for column in columns:
            escaped = re.escape(column)
            if re.search(rf"(?<![\w.]){escaped}\s*=(?!=)", direct):
                continue
            if re.search(rf"\.{escaped}\s*=(?!=)", direct):
                continue
            if re.search(rf"[\"']{escaped}[\"']", dynamic):
                continue
            unwritten.add((fname, column))
    return frozenset(unwritten)


def test_every_unwritten_column_has_an_owner():
    """No column may sit unwritten without an owner in the register."""
    unwritten = _unwritten_columns()
    unclaimed = sorted(unwritten - set(EXPECTED_UNWRITTEN))
    assert not unclaimed, (
        "These mapped columns have no writer and no entry in EXPECTED_UNWRITTEN. "
        "Wire them at their natural write point, or add an (owner, reason) so the "
        "schema stops claiming capability the code does not have:\n  "
        + "\n  ".join(f"{f}::{c}" for f, c in unclaimed)
    )


def test_register_does_not_claim_closed_gaps():
    """A column that gained a writer must leave the register."""
    unwritten = _unwritten_columns()
    stale = sorted(set(EXPECTED_UNWRITTEN) - unwritten)
    assert not stale, (
        "These columns are listed as unwritten but now have a writer. Remove them "
        "from EXPECTED_UNWRITTEN (and close the owning backlog item if that was "
        "the last column it owned):\n  "
        + "\n  ".join(f"{f}::{c}" for f, c in stale)
    )


def test_register_entries_are_specific():
    """Every entry carries a real owner and a real reason."""
    for key, value in EXPECTED_UNWRITTEN.items():
        owner, reason = value
        assert owner.strip(), f"{key} has no owner"
        assert len(reason.strip()) > 15, f"{key} needs a reason, not a placeholder: {reason!r}"
