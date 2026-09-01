"""Run the validator cascade over one content revision.

Pure: takes content, returns a verdict. No database, no session, no lifecycle
state. That separation is what lets the same assessment run identically from
the generation worker, the manual generation endpoint, a draft edit, a
rollback, and a backfill script — which is the thing the plan asks for when it
says these paths must not maintain separate quality logic.

The one rule this module exists to enforce: **a validator that fails cannot
produce a pass.** An exception is caught and recorded as ``error``; a validator
that declines to decide is ``inconclusive``; and ``resolve_overall`` takes the
worst state, never an average. Every previous version of this idea in this
codebase has been a boolean somewhere, and a boolean has no way to say "the
evaluator crashed".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

import structlog

from contextedge.quality.registry import (
    VALIDATOR_BUNDLE_VERSION,
    Finding,
    ValidationContext,
    registered_validators,
)
from contextedge.quality.states import (
    BLOCKING_SEVERITIES,
    CATEGORY_VALIDATOR_ERROR,
    DIMENSIONS,
    STATE_ERROR,
    STATE_INCONCLUSIVE,
    resolve_overall,
    worse_state,
)

logger = structlog.get_logger()


@dataclass
class AssessmentOutcome:
    overall_state: str
    dimension_states: dict[str, str]
    findings: list[Finding] = field(default_factory=list)
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None
    validator_bundle_version: str = VALIDATOR_BUNDLE_VERSION

    @property
    def blocking_findings(self) -> list[Finding]:
        """Findings that actually failed a dimension.

        Reads the severity set rather than restating it. The first version
        listed ``critical`` alone, which quietly disagreed with
        ``state_for_findings`` — a major finding failed the dimension but was
        absent from the list named after the thing that blocks. Nothing called
        this yet, so nothing was wrong on screen; the frontend panel asking
        "what is blocking?" would have been the first to find out, and it
        would have shown a reviewer a shorter list than the verdict was based
        on.
        """
        return [f for f in self.findings if f.severity in BLOCKING_SEVERITIES]

    def findings_as_dicts(self) -> list[dict]:
        return [finding.as_dict() for finding in self.findings]


def assess(context: ValidationContext) -> AssessmentOutcome:
    """Run every registered validator and combine their verdicts.

    Dimensions no validator claimed are recorded as ``inconclusive`` rather
    than omitted. An omitted dimension reads as clean to anything scanning the
    map, and "we did not check safety" must never look like "safety is fine".
    """
    started_at = datetime.now(UTC)
    dimension_states: dict[str, str] = {}
    findings: list[Finding] = []
    claimed: set[str] = set()

    for validator in registered_validators():
        claimed.update(validator.dimensions)
        try:
            result = validator.fn(context)
        except Exception as exc:  # noqa: BLE001 - deliberate: see module docstring
            logger.exception(
                "playbook_quality.validator_failed",
                validator=validator.name,
                playbook_id=context.playbook_id,
                error=str(exc)[:400],
            )
            for dimension in validator.dimensions:
                dimension_states[dimension] = worse_state(
                    dimension_states.get(dimension, STATE_ERROR), STATE_ERROR
                )
                findings.append(
                    Finding(
                        category=CATEGORY_VALIDATOR_ERROR,
                        dimension=dimension,
                        # `major`, not `critical`: our evaluator broke, which
                        # is not evidence the playbook is dangerous. The
                        # `error` state already prevents this being read as a
                        # pass; inflating the severity would send reviewers
                        # hunting a defect in content that may be fine.
                        severity="major",
                        explanation=(
                            f"Validator {validator.name!r} raised "
                            f"{type(exc).__name__}: {str(exc)[:200]}"
                        ),
                        validator=validator.name,
                    )
                )
            continue

        for dimension, state in result.dimension_states.items():
            dimension_states[dimension] = worse_state(
                dimension_states.get(dimension, state), state
            )
        findings.extend(result.findings)

    for dimension in DIMENSIONS:
        if dimension not in claimed:
            dimension_states.setdefault(dimension, STATE_INCONCLUSIVE)

    return AssessmentOutcome(
        overall_state=resolve_overall(dimension_states),
        dimension_states=dimension_states,
        findings=findings,
        started_at=started_at,
        completed_at=datetime.now(UTC),
    )


def error_outcome(reason: str) -> AssessmentOutcome:
    """The outcome when assessment could not be attempted at all.

    Used when building the context itself fails — a missing version, an
    unreadable content blob. Still a persisted assessment: "we tried and could
    not" is a fact the review queue needs, and silently writing nothing leaves
    the playbook indistinguishable from one never submitted.
    """
    now = datetime.now(UTC)
    states = {dimension: STATE_ERROR for dimension in DIMENSIONS}
    return AssessmentOutcome(
        overall_state=STATE_ERROR,
        dimension_states=states,
        findings=[
            Finding(
                category=CATEGORY_VALIDATOR_ERROR,
                dimension="structure",
                severity="major",
                explanation=reason,
                validator="orchestrator",
            )
        ],
        started_at=now,
        completed_at=now,
    )
