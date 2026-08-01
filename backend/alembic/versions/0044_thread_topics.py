"""Thread-topic state (backlog A3).

A conversation thread is "about" a case — and most messages in it never
name the ticket. The topic row carries that state: which case the
thread currently anchors to, whether the anchor is still provisional
(incident discussed before any ticket exists), what set it, and since
when. Un-anchored messages inherit the topic as a membership; a thread
that finally gets its ticket unifies retroactively.

Topics are set by explicit anchors (a resolved non-digest ticket
mention, a correction's propagated case) — never by mere mentions; the
digest guard holds here too.

Additive and re-runnable.

Revision ID: 0044_thread_topics
Revises: 0043_entity_traits
Create Date: 2026-08-03 10:00:00.000000
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0044_thread_topics"
down_revision: Union[str, None] = "0043_entity_traits"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS thread_topics (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL REFERENCES tenants(id),
            thread_id UUID NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
            canonical_case_id UUID NOT NULL,
            is_provisional BOOLEAN NOT NULL DEFAULT false,
            set_by VARCHAR(30) NOT NULL,
            confidence FLOAT NOT NULL DEFAULT 0.75,
            since TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_thread_topic UNIQUE (thread_id)
        );
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_thread_topics_tenant_case
        ON thread_topics (tenant_id, canonical_case_id);
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS thread_topics;")
