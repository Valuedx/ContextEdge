"""Aggregating criterion observations into a verdict (F9).

The old sweep asked one question and answered in one of three words. Its worst
case was silent: a CI that had stopped reporting looked exactly like a service
that recovered, and both fed the cohort counters and the knowledge-support
signal as success.

The aggregation below exists to make that case say ``inconclusive``. The rest
of the rules follow from one idea: **absence of bad news is weaker evidence
than presence of good news**, and a verdict should say which one it had.

Rules, in the order they are applied:

1. Any failing criterion, with a positive signal also passing → PARTIAL_SUCCESS.
   Something recovered and something recurred; reporting either alone is wrong.
2. Any failing criterion, no positive pass → FAILED. Rollback is recommended
   when what failed is a recurrence (``incident_absence``): there is a change
   to consider undoing. When nothing failed by recurrence, escalation is
   flagged instead — nothing obvious to undo means a human should look.
3. Nothing failed, at least one pass, nothing left inconclusive → SUCCESS.
4. Nothing failed, at least one pass, something INCONCLUSIVE → MONITOR_REQUIRED.
   Good news with a question still open.
5. Nothing failed, nothing passed → INCONCLUSIVE. **This is the case the old
   sweep called ``verified``.**

``not_observable`` and ``inconclusive`` are deliberately not the same thing.
``not_observable`` means the criterion could not apply — there was no
conversation to read, no CI to watch — and it neither supports nor undermines
a verdict, so it does not hold back a SUCCESS the other criteria earned.
``inconclusive`` means the criterion DID apply and could not decide, which is
an open question and does hold the verdict at MONITOR_REQUIRED. Collapsing the
two would demote every telemetry-verified run that happened to have a quiet
chat thread, which would throw away the signal 0036 shipped.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from contextedge.models.verification import (
    OBSERVATION_STATUSES,
    POSITIVE_CRITERION_TYPES,
)

# How long a ``monitor_required`` verdict asks the follow-up watch to run.
# Four hours: long enough that a slow recurrence surfaces inside it, short
# enough that an operator still associates the alert with the change.
MONITOR_WINDOW_SEC = 4 * 60 * 60


@dataclass(slots=True)
class CriterionResult:
    """One criterion, evaluated."""

    criterion_type: str
    criterion_name: str
    status: str
    criterion_params: dict[str, Any] = field(default_factory=dict)
    observed_value: dict[str, Any] | None = None
    detail: str | None = None
    window_start: datetime | None = None
    window_end: datetime | None = None

    def __post_init__(self) -> None:
        if self.status not in OBSERVATION_STATUSES:
            raise ValueError(
                f"status must be one of {OBSERVATION_STATUSES}, got {self.status!r}"
            )

    @property
    def is_positive_signal(self) -> bool:
        return self.criterion_type in POSITIVE_CRITERION_TYPES


@dataclass(slots=True)
class Verdict:
    overall_result: str
    summary: str
    rollback_recommended: bool = False
    retry_recommended: bool = False
    escalation_required: bool = False
    # How long the follow-up watch should run, when the verdict asks for one.
    # Set only for ``monitor_required`` — every other result has already
    # decided, and a monitoring window on a settled verdict would be a
    # number nobody acts on.
    monitoring_window_hint: int | None = None


def aggregate(results: list[CriterionResult]) -> Verdict:
    """Turn criterion observations into one verdict. See the module docstring."""
    if not results:
        return Verdict(
            overall_result="inconclusive",
            summary="no criteria were evaluated",
            escalation_required=True,
        )

    failed = [r for r in results if r.status == "fail"]
    passed = [r for r in results if r.status == "pass"]
    # Only ``inconclusive`` is an open question. ``not_observable`` means the
    # criterion could not apply at all — see the module docstring.
    open_questions = [r for r in results if r.status == "inconclusive"]
    unusable = [r for r in results if r.status == "not_observable"]
    positive_passed = [r for r in passed if r.is_positive_signal]

    if failed:
        names = ", ".join(r.criterion_name for r in failed)
        recurrence = any(r.criterion_type == "incident_absence" for r in failed)
        if positive_passed:
            return Verdict(
                overall_result="partial_success",
                summary=(
                    f"{len(positive_passed)} confirmation(s) passed while {names} failed"
                ),
                rollback_recommended=False,
                retry_recommended=False,
                # Something both worked and did not. A human decides which half
                # matters; the system should not pick for them.
                escalation_required=True,
            )
        return Verdict(
            overall_result="rollback_required" if recurrence else "failed",
            summary=f"failed: {names}",
            rollback_recommended=recurrence,
            retry_recommended=not recurrence,
            escalation_required=not recurrence,
        )

    if passed and not open_questions:
        kind = "confirmed" if positive_passed else "no recurrence observed"
        return Verdict(overall_result="success", summary=f"all criteria passed ({kind})")

    if passed and open_questions:
        names = ", ".join(r.criterion_name for r in open_questions)
        return Verdict(
            overall_result="monitor_required",
            summary=f"passed what could be observed; still open: {names}",
            monitoring_window_hint=MONITOR_WINDOW_SEC,
        )

    names = ", ".join(
        r.criterion_name for r in (open_questions + unusable)
    ) or "nothing observable"
    return Verdict(
        overall_result="inconclusive",
        summary=(
            f"nothing could be established ({names}). Absence of a signal from a "
            "source that never produced one is not evidence the fix held."
        ),
        escalation_required=True,
    )


# The three legacy words ``ExecutionRun.verification_status`` can hold. The
# column stays for the sweep queue, the cohort counters and the knowledge
# support signal; the assessment is the full answer.
LEGACY_STATUS_BY_RESULT: dict[str, str] = {
    "success": "verified",
    "partial_success": "failed",
    "failed": "failed",
    "rollback_required": "failed",
    "inconclusive": "unverifiable",
    "monitor_required": "unverifiable",
}


def legacy_status(overall_result: str) -> str:
    """Map a verdict onto the three-word column downstream still reads.

    ``partial_success`` maps to ``failed`` deliberately: the learning loop
    must not count a half-fix as a verified success, which is the same reason
    the projection has a ``partially_validated_fix`` edge type rather than
    folding partials into ``validated_fix``.

    ``monitor_required`` maps to ``unverifiable`` rather than ``verified`` for
    the same reason — good news with unresolved coverage is not a verified
    outcome, and inflating it is what F9 exists to stop.
    """
    return LEGACY_STATUS_BY_RESULT.get(overall_result, "unverifiable")
