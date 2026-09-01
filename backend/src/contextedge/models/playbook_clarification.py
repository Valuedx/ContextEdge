"""Clarification rounds and the questions they ask.

Migration 0095. The design argument for each table is in that migration's
docstring; the short version is that a round is one iteration of the loop — it
has a state machine, a cost, and a decision — and a question is one defect
turned into something a person can answer.

``gap_key`` is the column that makes the loop converge rather than repeat: the
same defect surviving into the next round hashes to the same key and carries its
answer forward. See ``contextedge.quality.clarification.gaps``.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from contextedge.models.base import Base, TenantOwnedMixin
from contextedge.quality.clarification.states import (
    ANSWER_KINDS,
    ANSWER_SOURCES,
    GAP_ORIGINS,
    OBLIGATIONS,
    QUESTION_STATUSES,
    ROUND_STATUSES,
)
from contextedge.quality.states import TARGET_KINDS


def _check(column: str, values: tuple[str, ...]) -> str:
    joined = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({joined})"


def _nullable_check(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IS NULL OR {_check(column, values)}"


class PlaybookClarificationRound(Base, TenantOwnedMixin):
    """One iteration of the clarification loop.

    ``content_hash`` records which content the questions are about. A round
    opened against text that has since been edited is asking about a playbook
    nobody can see any more, and the panel must say so rather than presenting
    stale questions as current — the same failure mode
    ``PlaybookQualityAssessment.content_hash`` exists to prevent.
    """

    __tablename__ = "playbook_clarification_rounds"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_pclr_tenant_id_id"),
        UniqueConstraint(
            "tenant_id", "playbook_id", "round_number", name="uq_pclr_tenant_playbook_round"
        ),
        CheckConstraint(_check("status", ROUND_STATUSES), name="ck_pcr_status"),
        CheckConstraint("round_number > 0", name="ck_pcr_round_number_positive"),
        CheckConstraint(
            "regeneration_count >= 0", name="ck_pclr_regeneration_count_non_negative"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    playbook_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("playbooks.id", ondelete="CASCADE"), nullable=False
    )
    round_number: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    assessment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("playbook_quality_assessments.id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(String(20), server_default="open", nullable=False)
    # gap_count >= question_count: gaps resolved from context or the KB never
    # became questions anyone had to answer, and the difference is the measure
    # of whether KB-first is earning its keep.
    gap_count: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    question_count: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    mandatory_count: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    resolved_from_kb_count: Mapped[int] = mapped_column(
        Integer, server_default="0", nullable=False
    )
    resolved_from_context_count: Mapped[int] = mapped_column(
        Integer, server_default="0", nullable=False
    )
    kb_status: Mapped[str] = mapped_column(String(20), server_default="ok", nullable=False)
    # How many times a reviewer asked for the questions to be rewritten. Bounded
    # in the service: each one is a generation call, and a button with no
    # counter behind it is an unbounded spend control shaped like an affordance.
    regeneration_count: Mapped[int] = mapped_column(
        Integer, server_default="0", nullable=False
    )
    prompt_name: Mapped[str | None] = mapped_column(String(60), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(20), nullable=True)
    model_provenance: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    generation_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    applied_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    opened_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    questions: Mapped[list["PlaybookClarificationQuestion"]] = relationship(
        back_populates="round", cascade="all, delete-orphan"
    )


class PlaybookClarificationQuestion(Base, TenantOwnedMixin):
    """One question and its answer.

    Not split into a separate answers table: an answer is one-to-one with a
    question inside a round and cannot exceed that cardinality, so the split
    would buy a join and nothing else. History lives at the round level — a new
    round copies the answer forward onto a new row, so what was asked in round 2
    stays answerable after round 3 rewrote the playbook.
    """

    __tablename__ = "playbook_clarification_questions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_pclq_tenant_id_id"),
        UniqueConstraint("tenant_id", "round_id", "gap_key", name="uq_pclq_round_gap"),
        CheckConstraint(_check("status", QUESTION_STATUSES), name="ck_pclq_status"),
        CheckConstraint(_check("obligation", OBLIGATIONS), name="ck_pclq_obligation"),
        CheckConstraint(_check("answer_kind", ANSWER_KINDS), name="ck_pclq_answer_kind"),
        CheckConstraint(_check("target_kind", TARGET_KINDS), name="ck_pclq_target_kind"),
        CheckConstraint(_check("gap_origin", GAP_ORIGINS), name="ck_pclq_gap_origin"),
        CheckConstraint(
            _nullable_check("answer_source", ANSWER_SOURCES), name="ck_pclq_answer_source"
        ),
        # An answered question with no text would silently satisfy a mandatory
        # obligation. Skipped and withdrawn questions legitimately have none.
        CheckConstraint(
            "status <> 'answered' OR answer_text IS NOT NULL",
            name="ck_pclq_answered_has_text",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    round_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("playbook_clarification_rounds.id", ondelete="CASCADE"),
        nullable=False,
    )
    playbook_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("playbooks.id", ondelete="CASCADE"), nullable=False
    )
    gap_key: Mapped[str] = mapped_column(String(64), nullable=False)
    gap_kind: Mapped[str] = mapped_column(String(60), nullable=False)
    gap_origin: Mapped[str] = mapped_column(String(20), nullable=False)
    source_finding_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    target_kind: Mapped[str] = mapped_column(
        String(20), server_default="playbook", nullable=False
    )
    target_ref: Mapped[str | None] = mapped_column(String(200), nullable=True)
    claim: Mapped[str | None] = mapped_column(Text, nullable=True)
    severity: Mapped[str | None] = mapped_column(String(20), nullable=True)
    question_text: Mapped[str] = mapped_column(Text, nullable=False)
    why_it_matters: Mapped[str | None] = mapped_column(Text, nullable=True)
    obligation: Mapped[str] = mapped_column(String(20), server_default="optional", nullable=False)
    answer_kind: Mapped[str] = mapped_column(String(20), server_default="text", nullable=False)
    choices: Mapped[list] = mapped_column(JSONB, server_default="[]", nullable=False)
    expected_format: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(30), server_default="open", nullable=False)
    answer_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    answer_source: Mapped[str | None] = mapped_column(String(20), nullable=True)
    answer_provenance: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    answered_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    round: Mapped["PlaybookClarificationRound"] = relationship(back_populates="questions")
