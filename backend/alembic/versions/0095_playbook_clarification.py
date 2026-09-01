"""Playbook clarification loop: rounds of AI-generated questions and their answers.

Phase C1 of docs/PLAYBOOK_CLARIFICATION_LOOP_PLAN.md.

Two tables, and the reason there are two rather than one or three:

``playbook_clarification_rounds``
    One iteration of the loop. A round is the unit that has a state machine, a
    cost (one retrieval plus one generation call), and a decision attached to
    it, so it cannot be a column on the playbook. It records which content
    revision it was opened against — a round opened against text that has since
    been edited is asking about a playbook nobody can see any more, exactly the
    failure ``playbook_quality_assessments.content_hash`` exists to prevent.

``playbook_clarification_questions``
    One question, and its answer. Not two tables: an answer is one-to-one with
    a question inside a round and cannot exceed that cardinality, so splitting
    them would buy a join and nothing else. History is preserved at the round
    level instead — a new round copies forward the answer under a new row, so
    "what did we ask in round 2 and what were we told" stays answerable after
    round 3 rewrites the playbook.

``gap_key`` is the load-bearing column. It is a stable hash of the defect a
question is about, so the same gap surviving into the next round keeps its
answer instead of being re-asked. Without it "repeat as many times as required"
means "ask the same question forever".

Nothing here enforces anything. Rounds are advisory; the playbook can be
approved with every question unanswered, exactly as it can today.

Revision ID: 0095_playbook_clarification
Revises: 0094_playbook_quality_foundation
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0095_playbook_clarification"
down_revision = "0094_playbook_quality_foundation"
branch_labels = None
depends_on = None


# Kept in sync with contextedge.quality.clarification.states. Duplicated as
# literals on purpose: a migration must not import application code, or it
# stops being replayable against a checkout where that code has moved on.
_ROUND_STATUSES = (
    "open",
    "answered",
    "applied",
    "satisfied",
    "exhausted",
    "abandoned",
)
_QUESTION_STATUSES = (
    "open",
    "answered",
    "skipped",
    "resolved_from_kb",
    "resolved_from_context",
    "withdrawn",
)
_OBLIGATIONS = ("mandatory", "optional")
_ANSWER_KINDS = ("text", "choice", "boolean", "list")
_ANSWER_SOURCES = ("human", "kb", "context", "carried")
_TARGET_KINDS = ("playbook", "field", "step")
_GAP_ORIGINS = ("finding", "contract", "gate", "structure")


def _in(column: str, values: tuple[str, ...]) -> str:
    joined = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({joined})"


def _nullable_in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IS NULL OR {_in(column, values)}"


def upgrade() -> None:
    op.execute(sa.text("SELECT set_config('app.bypass_rls', 'on', false)"))

    # ------------------------------------------------------------------ rounds
    op.create_table(
        "playbook_clarification_rounds",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("playbook_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("round_number", sa.Integer(), nullable=False),
        # Which content the questions are about. Compared against the live hash
        # so the panel can say "these questions are about an earlier draft"
        # rather than presenting them as current.
        sa.Column("content_hash", sa.String(64), nullable=False),
        # NULL when the round was opened before any assessment existed, which is
        # a legitimate state (a playbook with no version yet) and not the same
        # as an assessment that was not recorded.
        sa.Column("assessment_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="open"),
        # Counters, so a round's cost and yield are readable without loading
        # every question row. gap_count >= question_count: gaps resolved from
        # context or the KB never became questions a person had to answer.
        sa.Column("gap_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("question_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("mandatory_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("resolved_from_kb_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "resolved_from_context_count", sa.Integer(), nullable=False, server_default="0"
        ),
        # ok | no_results | retrieval_failed. A round with no KB hits because
        # the index was down must not read like one where the KB had nothing.
        sa.Column("kb_status", sa.String(20), nullable=False, server_default="ok"),
        sa.Column("prompt_name", sa.String(60), nullable=True),
        sa.Column("prompt_version", sa.String(20), nullable=True),
        sa.Column("model_provenance", postgresql.JSONB(), nullable=True),
        # Why question generation produced nothing, when it produced nothing.
        # An empty round with no reason reads as "nothing to ask", which is a
        # different and much more reassuring statement than the truth.
        sa.Column("generation_error", sa.Text(), nullable=True),
        sa.Column("applied_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("opened_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "opened_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["playbook_id"], ["playbooks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["assessment_id"], ["playbook_quality_assessments.id"], ondelete="SET NULL"
        ),
        sa.CheckConstraint(_in("status", _ROUND_STATUSES), name="ck_pcr_status"),
        sa.CheckConstraint("round_number > 0", name="ck_pcr_round_number_positive"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_pclr_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id", "playbook_id", "round_number", name="uq_pclr_tenant_playbook_round"
        ),
    )
    op.create_index(
        "ix_playbook_clarification_rounds_tenant_id",
        "playbook_clarification_rounds",
        ["tenant_id"],
    )
    op.create_index(
        "ix_pclr_playbook_opened",
        "playbook_clarification_rounds",
        ["playbook_id", sa.text("opened_at DESC")],
    )
    # At most one live round per playbook. Two open rounds means two sets of
    # questions about the same defects, and an answer recorded against whichever
    # one the panel happened to load.
    op.create_index(
        "ix_pclr_one_live_round",
        "playbook_clarification_rounds",
        ["tenant_id", "playbook_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('open', 'answered')"),
    )
    op.create_foreign_key(
        "fk_pclr_tenant_playbook",
        "playbook_clarification_rounds",
        "playbooks",
        ["tenant_id", "playbook_id"],
        ["tenant_id", "id"],
        ondelete="CASCADE",
    )
    _enable_rls("playbook_clarification_rounds")

    # --------------------------------------------------------------- questions
    op.create_table(
        "playbook_clarification_questions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("round_id", postgresql.UUID(as_uuid=True), nullable=False),
        # Denormalised so "every outstanding mandatory question for this
        # playbook" is one indexed read rather than a join through rounds on
        # the review queue's hot path.
        sa.Column("playbook_id", postgresql.UUID(as_uuid=True), nullable=False),
        # Stable identity of the defect this question is about. The column that
        # makes the loop converge — see the module docstring.
        sa.Column("gap_key", sa.String(64), nullable=False),
        sa.Column("gap_kind", sa.String(60), nullable=False),
        sa.Column("gap_origin", sa.String(20), nullable=False),
        sa.Column("source_finding_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("target_kind", sa.String(20), nullable=False, server_default="playbook"),
        sa.Column("target_ref", sa.String(200), nullable=True),
        sa.Column("claim", sa.Text(), nullable=True),
        sa.Column("severity", sa.String(20), nullable=True),
        # The generated question. Never a template — see plan §5.
        sa.Column("question_text", sa.Text(), nullable=False),
        sa.Column("why_it_matters", sa.Text(), nullable=True),
        sa.Column("obligation", sa.String(20), nullable=False, server_default="optional"),
        sa.Column("answer_kind", sa.String(20), nullable=False, server_default="text"),
        sa.Column("choices", postgresql.JSONB(), server_default="[]", nullable=False),
        sa.Column("expected_format", sa.Text(), nullable=True),
        sa.Column("status", sa.String(30), nullable=False, server_default="open"),
        sa.Column("answer_text", sa.Text(), nullable=True),
        sa.Column("answer_source", sa.String(20), nullable=True),
        # Where a non-human answer came from: evidence_id, section_ref, score.
        # An unattributed KB prefill is indistinguishable from a model guess,
        # and would be approved as though a person had supplied it.
        sa.Column("answer_provenance", postgresql.JSONB(), nullable=True),
        sa.Column("answered_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["round_id"], ["playbook_clarification_rounds.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["playbook_id"], ["playbooks.id"], ondelete="CASCADE"),
        sa.CheckConstraint(_in("status", _QUESTION_STATUSES), name="ck_pclq_status"),
        sa.CheckConstraint(_in("obligation", _OBLIGATIONS), name="ck_pclq_obligation"),
        sa.CheckConstraint(_in("answer_kind", _ANSWER_KINDS), name="ck_pclq_answer_kind"),
        sa.CheckConstraint(_in("target_kind", _TARGET_KINDS), name="ck_pclq_target_kind"),
        sa.CheckConstraint(_in("gap_origin", _GAP_ORIGINS), name="ck_pclq_gap_origin"),
        sa.CheckConstraint(
            _nullable_in("answer_source", _ANSWER_SOURCES), name="ck_pclq_answer_source"
        ),
        # An answered question with no answer text is a bookkeeping bug that
        # would silently satisfy a mandatory obligation. Skipped and withdrawn
        # questions legitimately have none.
        sa.CheckConstraint(
            "status <> 'answered' OR answer_text IS NOT NULL",
            name="ck_pclq_answered_has_text",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_pclq_tenant_id_id"),
        # One question per gap per round. The upsert path relies on this.
        sa.UniqueConstraint("tenant_id", "round_id", "gap_key", name="uq_pclq_round_gap"),
    )
    op.create_index(
        "ix_playbook_clarification_questions_tenant_id",
        "playbook_clarification_questions",
        ["tenant_id"],
    )
    op.create_index(
        "ix_pclq_round", "playbook_clarification_questions", ["round_id"]
    )
    # The review queue's question: "does this playbook still owe us answers?"
    op.create_index(
        "ix_pclq_outstanding_mandatory",
        "playbook_clarification_questions",
        ["tenant_id", "playbook_id"],
        postgresql_where=sa.text("status = 'open' AND obligation = 'mandatory'"),
    )
    op.create_index(
        "ix_pclq_gap_key", "playbook_clarification_questions", ["tenant_id", "gap_key"]
    )
    op.create_foreign_key(
        "fk_pclq_tenant_round",
        "playbook_clarification_questions",
        "playbook_clarification_rounds",
        ["tenant_id", "round_id"],
        ["tenant_id", "id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_pclq_tenant_playbook",
        "playbook_clarification_questions",
        "playbooks",
        ["tenant_id", "playbook_id"],
        ["tenant_id", "id"],
        ondelete="CASCADE",
    )
    _enable_rls("playbook_clarification_questions")


def downgrade() -> None:
    op.execute(sa.text("SELECT set_config('app.bypass_rls', 'on', false)"))
    op.drop_table("playbook_clarification_questions")
    op.drop_table("playbook_clarification_rounds")


def _enable_rls(table: str) -> None:
    op.execute(sa.text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
    op.execute(
        sa.text(
            f"""
            CREATE POLICY tenant_isolation ON {table}
            USING (
              current_setting('app.bypass_rls', true) = 'on'
              OR (
                COALESCE(current_setting('app.tenant_id', true), '') <> ''
                AND tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
              )
            )
            WITH CHECK (
              current_setting('app.bypass_rls', true) = 'on'
              OR (
                COALESCE(current_setting('app.tenant_id', true), '') <> ''
                AND tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
              )
            )
            """
        )
    )
