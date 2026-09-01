"""Turning quality defects into answerable information gaps.

A **gap** is one specific missing fact that keeps a playbook from being right.
Nothing in this module is a new inference: every gap is a re-reading of
something the quality system already computed and already persists — a finding,
a contract's unresolved requirements, a pre-generation gate's verdict.

Two things here decide whether the loop works at all.

**``gap_key`` is stable across rounds.** Round 2 recomputes gaps from scratch
against rewritten content. Without a key that survives a re-wording, every round
re-asks what a reviewer already answered, and "repeat as many times as required"
becomes "repeat forever". The key hashes the *normalised* claim, so a claim the
model paraphrased between rounds still hashes to the same gap. Getting this
wrong in either direction is the main failure mode of the feature: too strict
and the loop never converges, too loose and two different defects share one
answer.

**``ANSWERABLE_CATEGORIES`` is an allow-list, not a deny-list.** The default has
to be "do not bother a human". A validator that has not been built is a defect
in *us*; a branch pointing at a step that does not exist has a mechanical
repair. Asking a reviewer about either teaches them the questions are noise, and
after that they stop reading the ones that matter.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

from contextedge.quality.clarification.states import (
    GAP_ORIGIN_CONTRACT,
    GAP_ORIGIN_FINDING,
    GAP_ORIGIN_GATE,
    GAP_ORIGIN_STRUCTURE,
)
from contextedge.quality.states import (
    BLOCKING_SEVERITIES,
    CATEGORY_CITATION_UNRESOLVABLE,
    CATEGORY_CONTRADICTED_CLAIM,
    CATEGORY_EMPTY_PROCEDURE,
    CATEGORY_EVIDENCE_INSUFFICIENT,
    CATEGORY_INSUFFICIENT_DETAIL,
    CATEGORY_MISSING_OBLIGATION,
    CATEGORY_MISSING_REQUIRED_FIELD,
    CATEGORY_MISSING_ROLLBACK,
    CATEGORY_MISSING_VERIFICATION,
    CATEGORY_POLICY_DISCOURAGED,
    CATEGORY_POLICY_UNMET_CONDITION,
    CATEGORY_STALE_GROUNDING,
    CATEGORY_SUBJECT_MISMATCH,
    CATEGORY_SUBJECT_MULTIPLE,
    CATEGORY_SUBJECT_OVERBROAD,
    CATEGORY_TERMINOLOGY_NONCANONICAL,
    CATEGORY_UNSUPPORTED_CLAIM,
    CATEGORY_UNSUPPORTED_SPECIFICITY,
    CATEGORY_WRONG_ARTIFACT_TYPE,
    SEVERITY_MAJOR,
    SEVERITY_MINOR,
)

_WHITESPACE = re.compile(r"\s+")
_NON_SEMANTIC = re.compile(r"[^a-z0-9 ]+")

# How much of a claim participates in its identity. Long enough that two
# genuinely different obligations do not collide; short enough that a model
# appending a clause between rounds does not mint a new question about the
# same thing.
_CLAIM_KEY_CHARS = 240


# --- what a human can actually answer ---------------------------------------

# Finding categories a person can close by supplying a fact, mapped to the kind
# of gap they produce. Categories absent from this map deliberately produce no
# question:
#
#   validator_not_implemented / validator_error
#       A defect in our coverage, not in the content. Asking a reviewer about
#       it is asking them to apologise for our backlog.
#   invalid_structure / duplicate_step_identity / unreachable_step /
#   unresolvable_branch
#       Mechanical defects with mechanical repairs. The generator already
#       sanitizes branching; a question here asks a human to do arithmetic.
#   redundant_step / no_utility_step / oversized_artifact
#       Reductions, not additions. The fix is to delete, which the reviewer can
#       already do in the editor.
#   duplicate_artifact
#       A decision about two playbooks, not a fact about one.
ANSWERABLE_CATEGORIES: dict[str, str] = {
    CATEGORY_MISSING_OBLIGATION: "missing_required_action",
    CATEGORY_MISSING_VERIFICATION: "missing_verification",
    CATEGORY_MISSING_ROLLBACK: "missing_rollback",
    CATEGORY_INSUFFICIENT_DETAIL: "insufficient_detail",
    CATEGORY_UNSUPPORTED_CLAIM: "unsupported_claim",
    CATEGORY_UNSUPPORTED_SPECIFICITY: "unsupported_specificity",
    CATEGORY_CONTRADICTED_CLAIM: "contradicted_claim",
    CATEGORY_SUBJECT_OVERBROAD: "subject_scope",
    CATEGORY_SUBJECT_MULTIPLE: "subject_split",
    CATEGORY_SUBJECT_MISMATCH: "subject_step_mismatch",
    CATEGORY_POLICY_UNMET_CONDITION: "policy_condition",
    CATEGORY_POLICY_DISCOURAGED: "policy_alternative",
    CATEGORY_EMPTY_PROCEDURE: "empty_procedure",
    CATEGORY_MISSING_REQUIRED_FIELD: "missing_field",
    CATEGORY_EVIDENCE_INSUFFICIENT: "evidence_gap",
    CATEGORY_CITATION_UNRESOLVABLE: "citation_gap",
    CATEGORY_STALE_GROUNDING: "stale_grounding",
    CATEGORY_TERMINOLOGY_NONCANONICAL: "terminology",
    CATEGORY_WRONG_ARTIFACT_TYPE: "artifact_type",
}

# Pre-generation gate outcomes that leave a question for a person, and the gap
# kind each produces. ``ready_*`` outcomes are absent: there is nothing to ask.
GATE_GAP_KINDS: dict[str, str] = {
    "requires_additional_evidence": "evidence_gap",
    "requires_conflict_adjudication": "source_conflict",
    "requires_pattern_split": "subject_split",
}

# Gate outcomes that block generation. A gap from one of these is mandatory
# whatever the question generator proposes.
BLOCKING_GATE_OUTCOMES: frozenset[str] = frozenset(
    {"invalid_input", "requires_pattern_split", "requires_conflict_adjudication"}
)


def normalize_claim(text: str | None) -> str:
    """The part of a claim that participates in its identity.

    Lowercased, punctuation-stripped, whitespace-collapsed and truncated. A
    model that re-words "Restart the ingest service." into "restart the ingest
    service" between rounds must not mint a second question about the same
    obligation.
    """
    if not text:
        return ""
    lowered = _NON_SEMANTIC.sub(" ", str(text).lower())
    collapsed = _WHITESPACE.sub(" ", lowered).strip()
    return collapsed[:_CLAIM_KEY_CHARS]


def compute_gap_key(
    kind: str, target_kind: str, target_ref: str | None, claim: str | None
) -> str:
    """Stable identity of one gap. See the module docstring."""
    payload = f"{kind}|{target_kind}|{target_ref or ''}|{normalize_claim(claim)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


@dataclass(frozen=True)
class InformationGap:
    """One missing fact, with enough context to ask about it precisely."""

    kind: str
    origin: str
    claim: str | None = None
    target_kind: str = "playbook"
    target_ref: str | None = None
    severity: str = SEVERITY_MINOR
    # Whether this gap alone makes the playbook unfit. Set from the finding's
    # severity or the gate's outcome — never from the question generator, which
    # would then be able to mark its own blockers optional.
    blocking: bool = False
    source_finding_id: Any | None = None
    explanation: str | None = None
    context: dict[str, Any] = field(default_factory=dict)

    @property
    def gap_key(self) -> str:
        return compute_gap_key(self.kind, self.target_kind, self.target_ref, self.claim)

    def as_prompt_dict(self) -> dict[str, Any]:
        """What the question generator is allowed to see about this gap.

        Deliberately not the whole dataclass: the generator has no business
        knowing the finding's database id, and a prompt that carries one invites
        the model to echo it into question text a reviewer cannot open.
        """
        return {
            "gap_key": self.gap_key,
            "kind": self.kind,
            "origin": self.origin,
            "target_kind": self.target_kind,
            "target_ref": self.target_ref,
            "claim": (self.claim or "")[:600],
            "severity": self.severity,
            "blocking": self.blocking,
            "why_we_noticed": (self.explanation or "")[:400],
        }


def _attr(obj: Any, name: str, default: Any = None) -> Any:
    """Read a field off an ORM row or a plain dict, indifferently.

    Findings arrive as ORM rows from the database and as dicts from the
    orchestrator's in-memory outcome. Both are legitimate inputs and neither
    should have to be converted at the call site.
    """
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def gaps_from_findings(findings: list[Any]) -> list[InformationGap]:
    """Gaps implied by the current assessment's findings."""
    out: list[InformationGap] = []
    for finding in findings or []:
        category = _attr(finding, "category")
        kind = ANSWERABLE_CATEGORIES.get(str(category))
        if kind is None:
            continue
        severity = str(_attr(finding, "severity", SEVERITY_MINOR))
        # The claim is what the question will be about. When a finding carries
        # none, the explanation is the next best anchor — a gap with neither
        # would hash every finding of that category to one key and collapse
        # distinct defects into a single question.
        claim = _attr(finding, "claim") or _attr(finding, "explanation")
        out.append(
            InformationGap(
                kind=kind,
                origin=GAP_ORIGIN_FINDING,
                claim=str(claim) if claim else None,
                target_kind=str(_attr(finding, "target_kind", "playbook") or "playbook"),
                target_ref=_attr(finding, "target_ref"),
                severity=severity,
                blocking=severity in BLOCKING_SEVERITIES,
                source_finding_id=_attr(finding, "id"),
                explanation=_attr(finding, "explanation"),
                context={"category": category, "dimension": _attr(finding, "dimension")},
            )
        )
    return out


def gaps_from_contract(contract: dict[str, Any] | None) -> list[InformationGap]:
    """Gaps the contract itself already declared.

    ``unresolved_requirements`` and ``source_conflicts`` are the pre-generation
    pipeline writing down, at generation time, exactly what it could not settle.
    Re-deriving them here rather than at generation is deliberate: the reviewer
    who can answer them is looking at the draft, not at the worker log.
    """
    if not isinstance(contract, dict):
        return []
    out: list[InformationGap] = []

    for requirement in contract.get("unresolved_requirements") or []:
        text = str(requirement or "").strip()
        if not text:
            continue
        out.append(
            InformationGap(
                kind="unresolved_requirement",
                origin=GAP_ORIGIN_CONTRACT,
                claim=text,
                severity=SEVERITY_MAJOR,
                blocking=True,
                explanation="The contract recorded this as unresolved at generation time.",
            )
        )

    for conflict in contract.get("source_conflicts") or []:
        text = str(conflict or "").strip()
        if not text:
            continue
        out.append(
            InformationGap(
                kind="source_conflict",
                origin=GAP_ORIGIN_CONTRACT,
                claim=text,
                severity=SEVERITY_MAJOR,
                # A conflict is the one gap type the system must never resolve
                # on its own: picking a side between two sources is an
                # adjudication, and the whole point of surfacing it is that a
                # person makes it.
                blocking=True,
                explanation="Sources disagree; a reviewer has to decide which applies.",
            )
        )

    return out


def gaps_from_gate(evidence_refs: dict[str, Any] | None) -> list[InformationGap]:
    """The gap implied by the stored pre-generation gate verdict, if any."""
    if not isinstance(evidence_refs, dict):
        return []
    quality = evidence_refs.get("quality_contract")
    if not isinstance(quality, dict):
        return []
    gate = quality.get("gate")
    outcome = None
    reasons: list[str] = []
    if isinstance(gate, dict):
        outcome = gate.get("outcome")
        reasons = [str(r) for r in (gate.get("reasons") or []) if str(r).strip()]
    outcome = outcome or quality.get("outcome")
    kind = GATE_GAP_KINDS.get(str(outcome))
    if kind is None:
        return []

    return [
        InformationGap(
            kind=kind,
            origin=GAP_ORIGIN_GATE,
            claim="; ".join(reasons[:4]) or str(outcome),
            severity=SEVERITY_MAJOR,
            blocking=str(outcome) in BLOCKING_GATE_OUTCOMES,
            explanation=(
                f"Pre-generation gate returned {outcome}; the playbook was "
                "generated with that gap open."
            ),
            context={"gate_outcome": outcome},
        )
    ]


def gaps_from_structure(
    content: dict[str, Any] | None, contract: dict[str, Any] | None
) -> list[InformationGap]:
    """Field-level gaps visible from the artifact alone.

    Only two, and both only when the contract says the field should be there.
    A playbook with no rollback notes and no rollback obligation is complete,
    not incomplete, and asking about it would be inventing a requirement.
    """
    content = content or {}
    out: list[InformationGap] = []

    steps = [s for s in (content.get("steps") or []) if isinstance(s, dict)]
    if not steps:
        out.append(
            InformationGap(
                kind="empty_procedure",
                origin=GAP_ORIGIN_STRUCTURE,
                claim=str(content.get("title") or "").strip() or None,
                target_kind="field",
                target_ref="steps",
                severity="critical",
                blocking=True,
                explanation="The playbook has no steps, so there is no procedure to review.",
            )
        )

    has_rollback_obligation = bool(
        isinstance(contract, dict) and (contract.get("rollback_obligations") or [])
    )
    if has_rollback_obligation and not str(content.get("rollback_notes") or "").strip():
        out.append(
            InformationGap(
                kind="missing_rollback",
                origin=GAP_ORIGIN_STRUCTURE,
                claim="; ".join(
                    str(o) for o in (contract or {}).get("rollback_obligations", [])[:3]
                ),
                target_kind="field",
                target_ref="rollback_notes",
                severity=SEVERITY_MAJOR,
                blocking=True,
                explanation=(
                    "The contract carries rollback obligations but the playbook "
                    "records no rollback notes."
                ),
            )
        )

    return out


# Worst first, so a reviewer with time for three questions answers the three
# that matter. Blocking before non-blocking, then by severity, then by kind so
# the order is stable across rounds — a list that reshuffles between renders
# reads as though the questions changed.
_SEVERITY_RANK = {"critical": 0, "major": 1, "minor": 2, "info": 3}


def detect_gaps(
    *,
    content: dict[str, Any] | None,
    contract: dict[str, Any] | None,
    findings: list[Any] | None = None,
    evidence_refs: dict[str, Any] | None = None,
) -> list[InformationGap]:
    """Every answerable gap in this playbook, deduplicated and ordered.

    Deduplication is by ``gap_key`` and keeps the first occurrence, which — given
    the source order below — prefers the finding-derived gap over the
    contract-derived one for the same claim. That is the right preference: the
    finding knows which step it is about, and the contract does not.
    """
    collected: list[InformationGap] = []
    collected.extend(gaps_from_findings(findings or []))
    collected.extend(gaps_from_structure(content, contract))
    collected.extend(gaps_from_contract(contract))
    collected.extend(gaps_from_gate(evidence_refs))

    seen: set[str] = set()
    unique: list[InformationGap] = []
    for gap in collected:
        key = gap.gap_key
        if key in seen:
            continue
        seen.add(key)
        unique.append(gap)

    unique.sort(
        key=lambda g: (
            0 if g.blocking else 1,
            _SEVERITY_RANK.get(g.severity, 9),
            g.kind,
            g.target_ref or "",
            g.gap_key,
        )
    )
    return unique
