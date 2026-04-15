"""Add first-class decision trace tables: decisions, decision_options, decision_outcomes.

Revision ID: 0016_first_class_decisions
Revises: 0015_graph_edges_domain_id
Create Date: 2026-04-15 12:00:00.000000
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0016_first_class_decisions"
down_revision: Union[str, None] = "0015_graph_edges_domain_id"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS decisions (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL REFERENCES tenants(id),
            domain_id UUID NULL REFERENCES domains(id),
            session_id UUID NULL REFERENCES resolution_sessions(id),
            parent_decision_id UUID NULL REFERENCES decisions(id),
            decision_type VARCHAR(60) NOT NULL,
            agent_step VARCHAR(30) NOT NULL,
            actor_type VARCHAR(20) NOT NULL DEFAULT 'ai',
            actor_id UUID NULL,
            context_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
            evidence_summary JSONB NOT NULL DEFAULT '[]'::jsonb,
            rationale_summary TEXT NOT NULL,
            confidence FLOAT NULL,
            uncertainty_notes TEXT NULL,
            compact_trace TEXT NULL,
            explanation TEXT NULL,
            approval_required BOOLEAN NOT NULL DEFAULT false,
            policy_refs JSONB NOT NULL DEFAULT '[]'::jsonb,
            human_override BOOLEAN NOT NULL DEFAULT false,
            status VARCHAR(30) NOT NULL DEFAULT 'pending',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS ix_decisions_tenant_id
            ON decisions (tenant_id);
        CREATE INDEX IF NOT EXISTS ix_decisions_domain_id
            ON decisions (domain_id);
        CREATE INDEX IF NOT EXISTS ix_decisions_session_id
            ON decisions (session_id);
        CREATE INDEX IF NOT EXISTS ix_decisions_parent_decision_id
            ON decisions (parent_decision_id);
        CREATE INDEX IF NOT EXISTS ix_decisions_decision_type
            ON decisions (decision_type);
        CREATE INDEX IF NOT EXISTS ix_decisions_status
            ON decisions (status);
        CREATE INDEX IF NOT EXISTS ix_decisions_tenant_session
            ON decisions (tenant_id, session_id);
        CREATE INDEX IF NOT EXISTS ix_decisions_tenant_type
            ON decisions (tenant_id, decision_type);
        CREATE INDEX IF NOT EXISTS ix_decisions_context_snapshot
            ON decisions USING gin (context_snapshot);
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS decision_options (
            id UUID PRIMARY KEY,
            decision_id UUID NOT NULL REFERENCES decisions(id) ON DELETE CASCADE,
            tenant_id UUID NOT NULL REFERENCES tenants(id),
            action VARCHAR(200) NOT NULL,
            suitability FLOAT NULL,
            risk_level VARCHAR(20) NULL,
            preconditions JSONB NOT NULL DEFAULT '[]'::jsonb,
            rejection_reason TEXT NULL,
            selected BOOLEAN NOT NULL DEFAULT false,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS ix_decision_options_decision_id
            ON decision_options (decision_id);
        CREATE INDEX IF NOT EXISTS ix_decision_options_tenant_id
            ON decision_options (tenant_id);
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS decision_outcomes (
            id UUID PRIMARY KEY,
            decision_id UUID NOT NULL REFERENCES decisions(id) ON DELETE CASCADE,
            tenant_id UUID NOT NULL REFERENCES tenants(id),
            action_executed VARCHAR(200) NOT NULL,
            execution_result VARCHAR(30) NOT NULL,
            result_details JSONB NOT NULL DEFAULT '{}'::jsonb,
            follow_up_needed BOOLEAN NOT NULL DEFAULT false,
            follow_up_decision_id UUID NULL REFERENCES decisions(id),
            feedback_received TEXT NULL,
            feedback_by UUID NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS ix_decision_outcomes_decision_id
            ON decision_outcomes (decision_id);
        CREATE INDEX IF NOT EXISTS ix_decision_outcomes_tenant_id
            ON decision_outcomes (tenant_id);
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS decision_outcomes;")
    op.execute("DROP TABLE IF EXISTS decision_options;")
    op.execute("DROP TABLE IF EXISTS decisions;")
