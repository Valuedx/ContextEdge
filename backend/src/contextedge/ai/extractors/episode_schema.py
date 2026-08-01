"""Strict output schema for episode drafts (P4 source-aware synthesis).

The extractor used to trust whatever dict shape the model returned;
downstream then papered over gaps with ``.get()`` defaults. This module
is the single validation gate: every episode the LLM emits passes
through ``validate_episode`` before persistence.

Philosophy: strict about STRUCTURE (wrong types drop the episode with a
log — a malformed draft must not reach reviewers), lenient about
VOCABULARY (an unknown ``step_type`` coerces to "observation", numbers
clamp into range — a novel label is not a reason to lose a whole
incident story).
"""

from __future__ import annotations

import structlog
from pydantic import BaseModel, Field, ValidationError, field_validator

logger = structlog.get_logger()

STEP_TYPES = (
    "complaint",
    "diagnostic",
    "hypothesis",
    "action",
    "observation",
    "failed_step",
    "remediation",
    "escalation",
    "outcome",
)
RESULT_STATES = ("success", "failure", "inconclusive", "unknown")


class ContradictionAccount(BaseModel):
    evidence_id: str | None = None
    claim: str = Field(min_length=1)


class EpisodeContradiction(BaseModel):
    topic: str = Field(min_length=1)
    accounts: list[ContradictionAccount] = Field(min_length=2)


class EpisodeStepDraft(BaseModel):
    step_order: int = 0
    step_type: str = "observation"
    text: str = Field(min_length=1)
    observation: str | None = None
    result_state: str = "unknown"
    failed_flag: bool = False
    successful_flag: bool = False
    confidence: float = 0.5
    evidence_refs: list[str] | None = None

    @field_validator("step_type")
    @classmethod
    def _known_step_type(cls, v: str) -> str:
        return v if v in STEP_TYPES else "observation"

    @field_validator("result_state")
    @classmethod
    def _known_result_state(cls, v: str) -> str:
        return v if v in RESULT_STATES else "unknown"

    @field_validator("confidence")
    @classmethod
    def _clamp_confidence(cls, v: float) -> float:
        return min(max(v, 0.0), 1.0)


class EpisodeDraft(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    root_cause_summary: str | None = None
    final_outcome: str | None = None
    overall_confidence: float = 0.5
    evidence_refs: list[str] | None = None
    contradictions: list[EpisodeContradiction] = Field(default_factory=list)
    steps: list[EpisodeStepDraft] = Field(default_factory=list)

    @field_validator("overall_confidence")
    @classmethod
    def _clamp_confidence(cls, v: float) -> float:
        return min(max(v, 0.0), 1.0)

    @field_validator("contradictions", mode="before")
    @classmethod
    def _drop_invalid_contradictions(cls, v: object) -> list:
        """A malformed contradiction entry (missing topic, fewer than two
        accounts) drops silently — it must never cost the whole episode."""
        if not isinstance(v, list):
            return []
        kept = []
        for entry in v:
            try:
                kept.append(EpisodeContradiction.model_validate(entry))
            except ValidationError:
                continue
        return kept

    @field_validator("steps", mode="before")
    @classmethod
    def _drop_invalid_steps(cls, v: object) -> list:
        """Same leniency for steps: one empty-text step is model noise,
        not a reason to lose the other eight and the whole story."""
        if not isinstance(v, list):
            return []
        kept = []
        for entry in v:
            try:
                kept.append(EpisodeStepDraft.model_validate(entry))
            except ValidationError:
                continue
        return kept


def validate_episode(raw: dict) -> dict | None:
    """One raw model-emitted episode → validated plain dict, or None
    (with a warning log) when the structure is beyond saving."""
    try:
        return EpisodeDraft.model_validate(raw).model_dump()
    except ValidationError as exc:
        logger.warning(
            "episode_draft_invalid",
            title=str(raw.get("title", ""))[:120] if isinstance(raw, dict) else None,
            errors=exc.error_count(),
            first_error=str(exc.errors()[0].get("loc")) if exc.errors() else None,
        )
        return None
