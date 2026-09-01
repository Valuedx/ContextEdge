"""Wire shapes for the clarification loop.

Two fields decide how the panel must render and both are easy to skip, so they
are documented on the model rather than left to the client to work out:

- ``matches_current_content`` is false when the playbook has been edited since
  the round was opened. The questions then describe text nobody can see, and
  answering them writes an answer about a draft that no longer exists.
- ``answer_source`` distinguishes an answer a person typed from one prefilled
  from a KB article. Rendering them identically is how a retrieval score gets
  approved as a support decision.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class ClarificationQuestionResponse(BaseModel):
    id: UUID
    # Stable identity of the defect. Exposed so a client can correlate the same
    # question across rounds rather than treating each round's rows as new.
    gap_key: str
    gap_kind: str
    gap_origin: str
    target_kind: str
    target_ref: str | None = None
    claim: str | None = None
    severity: str | None = None
    question_text: str
    why_it_matters: str | None = None
    obligation: str
    answer_kind: str
    choices: list[str] = Field(default_factory=list)
    expected_format: str | None = None
    status: str
    answer_text: str | None = None
    answer_source: str | None = None
    answer_provenance: dict | None = None
    answered_at: datetime | None = None

    model_config = {"from_attributes": True}


class ClarificationRoundResponse(BaseModel):
    id: UUID
    round_number: int
    status: str
    content_hash: str
    assessment_id: UUID | None = None
    # gap_count >= question_count: the difference is what the KB and the
    # playbook itself answered without asking anyone, and it is the measure of
    # whether KB-first is earning its keep.
    gap_count: int
    question_count: int
    mandatory_count: int
    resolved_from_kb_count: int
    resolved_from_context_count: int
    kb_status: str
    # How many times a reviewer asked for these questions to be rewritten.
    # Surfaced so the panel can stop offering a button that will be refused.
    regeneration_count: int = 0
    prompt_name: str | None = None
    prompt_version: str | None = None
    # Populated when question generation failed or fell back. An empty round
    # with no reason reads as "nothing to ask", which is a very different and
    # much more reassuring statement than the truth.
    generation_error: str | None = None
    applied_version_id: UUID | None = None
    opened_at: datetime
    closed_at: datetime | None = None
    notes: str | None = None

    model_config = {"from_attributes": True}


class ClarificationSubmissionReadiness(BaseModel):
    """Whether a person could submit this now, and why not.

    Reporting only. The transition stays an explicit human action through
    ``POST /playbooks/{id}/transition``; nothing in the clarification loop moves
    a playbook forward on its own.
    """

    ready: bool
    blocked_reasons: list[str] = Field(default_factory=list)
    outstanding_mandatory: int = 0
    open_round_id: UUID | None = None
    open_round_status: str | None = None
    quality: dict = Field(default_factory=dict)


class PlaybookClarificationResponse(BaseModel):
    playbook_id: UUID
    content_hash: str
    round: ClarificationRoundResponse | None = None
    questions: list[ClarificationQuestionResponse] = Field(default_factory=list)
    matches_current_content: bool = False
    has_live_round: bool = False
    outstanding_mandatory: int = 0
    max_rounds: int = 5
    submission: ClarificationSubmissionReadiness


class ClarificationAnswerInput(BaseModel):
    question_id: UUID
    answer_text: str | None = None
    # A skip is a decision, not an absence: "nobody was asked" and "somebody was
    # asked and chose not to say" are different facts about a playbook. Refused
    # on a mandatory question.
    skip: bool = False


class ClarificationAnswersRequest(BaseModel):
    answers: list[ClarificationAnswerInput] = Field(min_length=1)


class ClarificationRegenerateRequest(BaseModel):
    """Ask again for the wording of the unanswered questions.

    ``guidance`` is the reviewer saying what was wrong with them — "too vague",
    "ask about the ordering, not the service name". It is the difference
    between a rewrite and a re-roll: at temperature 0 the same inputs produce
    the same output, so without it the second attempt is likely to be the first
    attempt again.
    """

    guidance: str | None = Field(default=None, max_length=2000)


class ClarificationApplyRequest(BaseModel):
    # Default true: the reviewer pressing Apply is asking to continue the loop,
    # and making them press a second button after every round is how a
    # five-round loop becomes a one-round one. Bounded by max_rounds.
    open_next: bool = True


class ClarificationApplyResponse(BaseModel):
    applied_round: ClarificationRoundResponse
    new_version_id: UUID
    new_semantic_version: str
    answers_applied: int
    next_round: ClarificationRoundResponse | None = None
    # True when the loop hit max_rounds with gaps still open. Terminal: this
    # needs a decision rather than another question.
    limit_reached: bool = False
    submission: ClarificationSubmissionReadiness
