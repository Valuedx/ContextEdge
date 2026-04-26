"""AE Ops Context Graph concept alignment.

Revision ID: 0029_ae_ops_concept_alignment
Revises: 0028_orm_ddl_drift_alignment
Create Date: 2026-04-27 00:00:00.000000

Aligns the ContextEdge schema with the AE Ops Context Graph design
(``ae_ops_context_graph_design.md``). Strictly additive — no rename,
no drop, no type change on existing columns. Existing code paths
continue to work unchanged because every new column is nullable and
every new table is independent.

What this migration adds:

New tables
----------
- ``entities`` — operational-noun graph node (workflow, agent_machine,
  schedule, output_location, …). Coexists with ``canonical_identities``
  which keeps its identity-resolution role.
- ``claims`` + ``claim_evidence`` — the missing "evidence-backed
  assertion with validation lifecycle" spine.
- ``decision_evidence`` — relational link supplementing the
  ``decisions.evidence_summary JSONB`` cache for query-by-evidence.
- ``action_policies`` — action-keyed policy with ``policy_result`` enum,
  separate from the generic ``tenant_policies`` config bucket.
- ``error_signatures`` + ``fix_patterns`` — normalised error fingerprint
  and statistical-fix recommender, separate from ``patterns`` /
  ``playbooks`` (which keep their existing semantics).
- ``case_outcomes`` + ``case_state_transitions`` — case-level outcome
  (vs per-decision ``decision_outcomes``) and lifecycle history.

Column additions
----------------
- ``resolution_sessions``: case_number/case_type/issue_type/title/
  description/priority/severity/environment + four entity-FK columns.
- ``evidence_items``: evidence_time/collected_by/source_type/redaction_status.
- ``decisions``: decision_intent/decision_summary/risk_level/policy_result.
- ``decision_trace_events``: decision_id FK + tool_name/tool_input_ref/
  tool_output_ref so the row can serve the cg_decision_step role.
- ``approval_requests``: action_name/approver_role/approval_channel/
  approval_note/recommended_by/executed_by/sod_check_status/
  sod_violation_reason + case_id/decision_trace_id FKs.
- ``execution_step_runs``: action_name/action_type/execution_mode/
  executed_by/idempotency_key/duplicate_check_status + case_id/
  decision_trace_id FKs. Partial unique index on idempotency_key.
- ``graph_edges``: valid_from/valid_to/confidence — temporal validity
  enables "what was true at incident time?" queries.

All ``CREATE TABLE`` statements use ``IF NOT EXISTS`` and all
``ALTER TABLE ADD COLUMN`` use ``IF NOT EXISTS`` — re-running on a
partially-applied DB is safe.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0029_ae_ops_concept_alignment"
down_revision: Union[str, None] = "0028_orm_ddl_drift_alignment"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. entities  (operational-noun graph nodes)
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS entities (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL REFERENCES tenants(id),
            domain_id UUID NULL REFERENCES domains(id),
            entity_type VARCHAR(50) NOT NULL,
            external_system VARCHAR(100) NULL,
            external_id VARCHAR(500) NULL,
            name VARCHAR(500) NOT NULL,
            environment VARCHAR(30) NULL,
            business_unit VARCHAR(100) NULL,
            data_domain VARCHAR(100) NULL,
            attributes JSONB NOT NULL DEFAULT '{}'::jsonb,
            source_ref JSONB NOT NULL DEFAULT '{}'::jsonb,
            confidence NUMERIC(5,4) NOT NULL DEFAULT 1.0,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            last_synced_at TIMESTAMPTZ NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_entities_type_system_external_id
                UNIQUE (entity_type, external_system, external_id)
        );
        CREATE INDEX IF NOT EXISTS ix_entities_tenant_id ON entities (tenant_id);
        CREATE INDEX IF NOT EXISTS ix_entities_domain_id ON entities (domain_id);
        CREATE INDEX IF NOT EXISTS ix_entities_entity_type ON entities (entity_type);
        CREATE INDEX IF NOT EXISTS ix_entities_environment ON entities (environment);
        CREATE INDEX IF NOT EXISTS ix_entities_attributes
            ON entities USING gin (attributes);
        CREATE INDEX IF NOT EXISTS ix_entities_tenant_type_env
            ON entities (tenant_id, entity_type, environment);
        """
    )

    # ------------------------------------------------------------------
    # 2. claims  +  claim_evidence  +  decision_evidence
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS claims (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL REFERENCES tenants(id),
            case_id UUID NULL REFERENCES resolution_sessions(id) ON DELETE CASCADE,
            claim_type VARCHAR(60) NOT NULL,
            claim_text TEXT NOT NULL,
            confidence NUMERIC(5,4) NOT NULL DEFAULT 0.5,
            created_by VARCHAR(120) NOT NULL,
            created_by_type VARCHAR(20) NOT NULL DEFAULT 'agent',
            validation_status VARCHAR(30) NOT NULL DEFAULT 'unverified',
            validated_by VARCHAR(120) NULL,
            validated_at TIMESTAMPTZ NULL,
            validation_note TEXT NULL,
            superseded_by_claim_id UUID NULL REFERENCES claims(id),
            attributes JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS ix_claims_tenant_id ON claims (tenant_id);
        CREATE INDEX IF NOT EXISTS ix_claims_case_id ON claims (case_id);
        CREATE INDEX IF NOT EXISTS ix_claims_claim_type ON claims (claim_type);
        CREATE INDEX IF NOT EXISTS ix_claims_validation_status ON claims (validation_status);
        CREATE INDEX IF NOT EXISTS ix_claims_confidence ON claims (confidence);
        CREATE INDEX IF NOT EXISTS ix_claims_attributes ON claims USING gin (attributes);
        -- Partial index for the "claims awaiting validation" dashboard tile.
        CREATE INDEX IF NOT EXISTS ix_claims_unverified
            ON claims (tenant_id, created_at DESC)
            WHERE validation_status IN ('unverified', 'machine_verified');
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS claim_evidence (
            id UUID PRIMARY KEY,
            claim_id UUID NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
            evidence_id UUID NOT NULL REFERENCES evidence_items(id) ON DELETE CASCADE,
            support_type VARCHAR(30) NOT NULL DEFAULT 'supports',
            weight NUMERIC(5,4) NOT NULL DEFAULT 1.0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_claim_evidence_pair UNIQUE (claim_id, evidence_id)
        );
        CREATE INDEX IF NOT EXISTS ix_claim_evidence_claim_id ON claim_evidence (claim_id);
        CREATE INDEX IF NOT EXISTS ix_claim_evidence_evidence_id ON claim_evidence (evidence_id);
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS decision_evidence (
            id UUID PRIMARY KEY,
            decision_id UUID NOT NULL REFERENCES decisions(id) ON DELETE CASCADE,
            evidence_id UUID NOT NULL REFERENCES evidence_items(id) ON DELETE CASCADE,
            support_type VARCHAR(30) NOT NULL DEFAULT 'supports',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_decision_evidence_pair UNIQUE (decision_id, evidence_id)
        );
        CREATE INDEX IF NOT EXISTS ix_decision_evidence_decision_id ON decision_evidence (decision_id);
        CREATE INDEX IF NOT EXISTS ix_decision_evidence_evidence_id ON decision_evidence (evidence_id);
        """
    )

    # ------------------------------------------------------------------
    # 3. action_policies  (action-keyed policy with policy_result verdict)
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS action_policies (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL REFERENCES tenants(id),
            policy_name VARCHAR(255) NOT NULL,
            action_name VARCHAR(120) NOT NULL,
            workflow_entity_id UUID NULL REFERENCES entities(id) ON DELETE SET NULL,
            environment VARCHAR(30) NULL,
            business_unit VARCHAR(100) NULL,
            data_domain VARCHAR(100) NULL,
            risk_level VARCHAR(20) NOT NULL,
            policy_result VARCHAR(40) NOT NULL,
            required_approver_roles JSONB NOT NULL DEFAULT '[]'::jsonb,
            allowed_execution_mode VARCHAR(40) NULL,
            conditions JSONB NOT NULL DEFAULT '{}'::jsonb,
            restrictions JSONB NOT NULL DEFAULT '{}'::jsonb,
            priority INTEGER NOT NULL DEFAULT 100,
            policy_scope VARCHAR(40) NULL,
            conflict_resolution VARCHAR(40) NOT NULL DEFAULT 'most_restrictive',
            description TEXT NULL,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS ix_action_policies_tenant_id ON action_policies (tenant_id);
        CREATE INDEX IF NOT EXISTS ix_action_policies_action_name ON action_policies (action_name);
        CREATE INDEX IF NOT EXISTS ix_action_policies_workflow ON action_policies (workflow_entity_id);
        CREATE INDEX IF NOT EXISTS ix_action_policies_policy_result ON action_policies (policy_result);
        CREATE INDEX IF NOT EXISTS ix_action_policies_lookup
            ON action_policies (tenant_id, action_name, workflow_entity_id, environment);
        CREATE INDEX IF NOT EXISTS ix_action_policies_conditions
            ON action_policies USING gin (conditions);
        """
    )

    # ------------------------------------------------------------------
    # 4. error_signatures  +  fix_patterns
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS error_signatures (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL REFERENCES tenants(id),
            signature_key VARCHAR(120) NOT NULL,
            display_name VARCHAR(500) NULL,
            error_type VARCHAR(80) NULL,
            normalized_message TEXT NULL,
            patterns JSONB NOT NULL DEFAULT '[]'::jsonb,
            example_messages JSONB NOT NULL DEFAULT '[]'::jsonb,
            usual_causes JSONB NOT NULL DEFAULT '[]'::jsonb,
            recommended_actions JSONB NOT NULL DEFAULT '[]'::jsonb,
            risk_notes JSONB NOT NULL DEFAULT '[]'::jsonb,
            success_count INTEGER NOT NULL DEFAULT 0,
            failure_count INTEGER NOT NULL DEFAULT 0,
            confidence NUMERIC(5,4) NOT NULL DEFAULT 0.5,
            pattern_id UUID NULL REFERENCES patterns(id) ON DELETE SET NULL,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_error_signatures_tenant_key UNIQUE (tenant_id, signature_key)
        );
        CREATE INDEX IF NOT EXISTS ix_error_signatures_tenant_id ON error_signatures (tenant_id);
        CREATE INDEX IF NOT EXISTS ix_error_signatures_signature_key ON error_signatures (signature_key);
        CREATE INDEX IF NOT EXISTS ix_error_signatures_error_type ON error_signatures (error_type);
        CREATE INDEX IF NOT EXISTS ix_error_signatures_pattern_id ON error_signatures (pattern_id);
        CREATE INDEX IF NOT EXISTS ix_error_signatures_patterns
            ON error_signatures USING gin (patterns);
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS fix_patterns (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL REFERENCES tenants(id),
            pattern_name VARCHAR(255) NOT NULL,
            issue_type VARCHAR(80) NOT NULL,
            workflow_entity_id UUID NULL REFERENCES entities(id) ON DELETE SET NULL,
            error_signature_id UUID NULL REFERENCES error_signatures(id) ON DELETE SET NULL,
            failed_step VARCHAR(255) NULL,
            recommended_fix TEXT NOT NULL,
            recommended_playbook_id UUID NULL REFERENCES playbooks(id) ON DELETE SET NULL,
            preconditions JSONB NOT NULL DEFAULT '[]'::jsonb,
            risk_level VARCHAR(20) NULL,
            approval_required BOOLEAN NOT NULL DEFAULT FALSE,
            source_case_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
            success_count INTEGER NOT NULL DEFAULT 0,
            failure_count INTEGER NOT NULL DEFAULT 0,
            confidence NUMERIC(5,4) NOT NULL DEFAULT 0.5,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            last_used_at TIMESTAMPTZ NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS ix_fix_patterns_tenant_id ON fix_patterns (tenant_id);
        CREATE INDEX IF NOT EXISTS ix_fix_patterns_issue_type ON fix_patterns (issue_type);
        CREATE INDEX IF NOT EXISTS ix_fix_patterns_workflow ON fix_patterns (workflow_entity_id);
        CREATE INDEX IF NOT EXISTS ix_fix_patterns_error_signature ON fix_patterns (error_signature_id);
        CREATE INDEX IF NOT EXISTS ix_fix_patterns_confidence ON fix_patterns (confidence);
        """
    )

    # ------------------------------------------------------------------
    # 5. case_outcomes  +  case_state_transitions
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS case_outcomes (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL REFERENCES tenants(id),
            case_id UUID NOT NULL REFERENCES resolution_sessions(id) ON DELETE CASCADE,
            outcome_status VARCHAR(40) NOT NULL,
            resolution_summary TEXT NULL,
            confirmed_root_cause TEXT NULL,
            successful_action VARCHAR(120) NULL,
            failed_actions JSONB NOT NULL DEFAULT '[]'::jsonb,
            user_confirmed BOOLEAN NULL,
            mttr_minutes NUMERIC(10,2) NULL,
            closed_by VARCHAR(120) NULL,
            closed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            should_create_or_update_pattern BOOLEAN NOT NULL DEFAULT TRUE,
            attributes JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS ix_case_outcomes_tenant_id ON case_outcomes (tenant_id);
        CREATE INDEX IF NOT EXISTS ix_case_outcomes_case_id ON case_outcomes (case_id);
        CREATE INDEX IF NOT EXISTS ix_case_outcomes_outcome_status ON case_outcomes (outcome_status);
        CREATE INDEX IF NOT EXISTS ix_case_outcomes_successful_action ON case_outcomes (successful_action);
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS case_state_transitions (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL REFERENCES tenants(id),
            case_id UUID NOT NULL REFERENCES resolution_sessions(id) ON DELETE CASCADE,
            from_status VARCHAR(40) NULL,
            to_status VARCHAR(40) NOT NULL,
            transition_reason TEXT NULL,
            transitioned_by VARCHAR(120) NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS ix_case_state_transitions_tenant_id ON case_state_transitions (tenant_id);
        CREATE INDEX IF NOT EXISTS ix_case_state_transitions_case_id ON case_state_transitions (case_id);
        CREATE INDEX IF NOT EXISTS ix_case_state_transitions_to_status ON case_state_transitions (to_status);
        """
    )

    # ------------------------------------------------------------------
    # 6. resolution_sessions  — case spine columns
    # ------------------------------------------------------------------
    op.execute(
        """
        ALTER TABLE resolution_sessions
            ADD COLUMN IF NOT EXISTS case_number VARCHAR(60) NULL,
            ADD COLUMN IF NOT EXISTS case_type VARCHAR(60) NULL,
            ADD COLUMN IF NOT EXISTS issue_type VARCHAR(80) NULL,
            ADD COLUMN IF NOT EXISTS title VARCHAR(500) NULL,
            ADD COLUMN IF NOT EXISTS description TEXT NULL,
            ADD COLUMN IF NOT EXISTS priority VARCHAR(20) NULL,
            ADD COLUMN IF NOT EXISTS severity VARCHAR(20) NULL,
            ADD COLUMN IF NOT EXISTS environment VARCHAR(30) NULL,
            ADD COLUMN IF NOT EXISTS user_entity_id UUID NULL,
            ADD COLUMN IF NOT EXISTS workflow_entity_id UUID NULL,
            ADD COLUMN IF NOT EXISTS request_entity_id UUID NULL,
            ADD COLUMN IF NOT EXISTS agent_entity_id UUID NULL;
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'fk_resolution_sessions_user_entity'
            ) THEN
                ALTER TABLE resolution_sessions
                    ADD CONSTRAINT fk_resolution_sessions_user_entity
                    FOREIGN KEY (user_entity_id) REFERENCES entities(id) ON DELETE SET NULL;
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'fk_resolution_sessions_workflow_entity'
            ) THEN
                ALTER TABLE resolution_sessions
                    ADD CONSTRAINT fk_resolution_sessions_workflow_entity
                    FOREIGN KEY (workflow_entity_id) REFERENCES entities(id) ON DELETE SET NULL;
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'fk_resolution_sessions_request_entity'
            ) THEN
                ALTER TABLE resolution_sessions
                    ADD CONSTRAINT fk_resolution_sessions_request_entity
                    FOREIGN KEY (request_entity_id) REFERENCES entities(id) ON DELETE SET NULL;
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'fk_resolution_sessions_agent_entity'
            ) THEN
                ALTER TABLE resolution_sessions
                    ADD CONSTRAINT fk_resolution_sessions_agent_entity
                    FOREIGN KEY (agent_entity_id) REFERENCES entities(id) ON DELETE SET NULL;
            END IF;
        END $$;
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_resolution_sessions_case_number
            ON resolution_sessions (case_number)
            WHERE case_number IS NOT NULL;
        CREATE INDEX IF NOT EXISTS ix_resolution_sessions_issue_type
            ON resolution_sessions (issue_type);
        CREATE INDEX IF NOT EXISTS ix_resolution_sessions_environment
            ON resolution_sessions (environment);
        CREATE INDEX IF NOT EXISTS ix_resolution_sessions_user_entity
            ON resolution_sessions (user_entity_id);
        CREATE INDEX IF NOT EXISTS ix_resolution_sessions_workflow_entity
            ON resolution_sessions (workflow_entity_id);
        CREATE INDEX IF NOT EXISTS ix_resolution_sessions_request_entity
            ON resolution_sessions (request_entity_id);
        CREATE INDEX IF NOT EXISTS ix_resolution_sessions_agent_entity
            ON resolution_sessions (agent_entity_id);
        """
    )

    # ------------------------------------------------------------------
    # 7. evidence_items  — temporal + lineage + redaction marker
    # ------------------------------------------------------------------
    op.execute(
        """
        ALTER TABLE evidence_items
            ADD COLUMN IF NOT EXISTS evidence_time TIMESTAMPTZ NULL,
            ADD COLUMN IF NOT EXISTS collected_by VARCHAR(120) NULL,
            ADD COLUMN IF NOT EXISTS source_type VARCHAR(50) NULL,
            ADD COLUMN IF NOT EXISTS redaction_status VARCHAR(30) NULL;
        CREATE INDEX IF NOT EXISTS ix_evidence_items_evidence_time
            ON evidence_items (evidence_time);
        CREATE INDEX IF NOT EXISTS ix_evidence_items_source_type
            ON evidence_items (source_type);
        """
    )

    # ------------------------------------------------------------------
    # 8. decisions  — governance axis + verdict
    # ------------------------------------------------------------------
    op.execute(
        """
        ALTER TABLE decisions
            ADD COLUMN IF NOT EXISTS decision_intent VARCHAR(40) NULL,
            ADD COLUMN IF NOT EXISTS decision_summary TEXT NULL,
            ADD COLUMN IF NOT EXISTS risk_level VARCHAR(20) NULL,
            ADD COLUMN IF NOT EXISTS policy_result VARCHAR(40) NULL;
        CREATE INDEX IF NOT EXISTS ix_decisions_decision_intent
            ON decisions (decision_intent);
        CREATE INDEX IF NOT EXISTS ix_decisions_policy_result
            ON decisions (policy_result);
        """
    )

    # ------------------------------------------------------------------
    # 9. decision_trace_events  — decision anchor + tool I/O refs
    # ------------------------------------------------------------------
    op.execute(
        """
        ALTER TABLE decision_trace_events
            ADD COLUMN IF NOT EXISTS decision_id UUID NULL,
            ADD COLUMN IF NOT EXISTS tool_name VARCHAR(200) NULL,
            ADD COLUMN IF NOT EXISTS tool_input_ref VARCHAR(500) NULL,
            ADD COLUMN IF NOT EXISTS tool_output_ref VARCHAR(500) NULL;
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'fk_decision_trace_events_decision'
            ) THEN
                ALTER TABLE decision_trace_events
                    ADD CONSTRAINT fk_decision_trace_events_decision
                    FOREIGN KEY (decision_id) REFERENCES decisions(id) ON DELETE CASCADE;
            END IF;
        END $$;
        CREATE INDEX IF NOT EXISTS ix_decision_trace_events_decision_id
            ON decision_trace_events (decision_id);
        """
    )

    # ------------------------------------------------------------------
    # 10. approval_requests  — case/decision anchor + role + channel + SoD
    # ------------------------------------------------------------------
    op.execute(
        """
        ALTER TABLE approval_requests
            ADD COLUMN IF NOT EXISTS action_name VARCHAR(120) NULL,
            ADD COLUMN IF NOT EXISTS approver_role VARCHAR(120) NULL,
            ADD COLUMN IF NOT EXISTS approval_channel VARCHAR(40) NULL,
            ADD COLUMN IF NOT EXISTS approval_note TEXT NULL,
            ADD COLUMN IF NOT EXISTS recommended_by UUID NULL,
            ADD COLUMN IF NOT EXISTS executed_by UUID NULL,
            ADD COLUMN IF NOT EXISTS sod_check_status VARCHAR(30) NULL,
            ADD COLUMN IF NOT EXISTS sod_violation_reason TEXT NULL,
            ADD COLUMN IF NOT EXISTS case_id UUID NULL,
            ADD COLUMN IF NOT EXISTS decision_trace_id UUID NULL;
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'fk_approval_requests_case'
            ) THEN
                ALTER TABLE approval_requests
                    ADD CONSTRAINT fk_approval_requests_case
                    FOREIGN KEY (case_id) REFERENCES resolution_sessions(id) ON DELETE SET NULL;
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'fk_approval_requests_decision_trace'
            ) THEN
                ALTER TABLE approval_requests
                    ADD CONSTRAINT fk_approval_requests_decision_trace
                    FOREIGN KEY (decision_trace_id) REFERENCES decisions(id) ON DELETE SET NULL;
            END IF;
        END $$;
        CREATE INDEX IF NOT EXISTS ix_approval_requests_action_name
            ON approval_requests (action_name);
        CREATE INDEX IF NOT EXISTS ix_approval_requests_case_id
            ON approval_requests (case_id);
        CREATE INDEX IF NOT EXISTS ix_approval_requests_decision_trace_id
            ON approval_requests (decision_trace_id);
        """
    )

    # ------------------------------------------------------------------
    # 11. execution_step_runs  — action_name + idempotency + anchors
    # ------------------------------------------------------------------
    op.execute(
        """
        ALTER TABLE execution_step_runs
            ADD COLUMN IF NOT EXISTS action_name VARCHAR(120) NULL,
            ADD COLUMN IF NOT EXISTS action_type VARCHAR(40) NULL,
            ADD COLUMN IF NOT EXISTS execution_mode VARCHAR(40) NULL,
            ADD COLUMN IF NOT EXISTS executed_by UUID NULL,
            ADD COLUMN IF NOT EXISTS idempotency_key VARCHAR(255) NULL,
            ADD COLUMN IF NOT EXISTS duplicate_check_status VARCHAR(30) NULL,
            ADD COLUMN IF NOT EXISTS case_id UUID NULL,
            ADD COLUMN IF NOT EXISTS decision_trace_id UUID NULL;
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'fk_execution_step_runs_case'
            ) THEN
                ALTER TABLE execution_step_runs
                    ADD CONSTRAINT fk_execution_step_runs_case
                    FOREIGN KEY (case_id) REFERENCES resolution_sessions(id) ON DELETE SET NULL;
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'fk_execution_step_runs_decision_trace'
            ) THEN
                ALTER TABLE execution_step_runs
                    ADD CONSTRAINT fk_execution_step_runs_decision_trace
                    FOREIGN KEY (decision_trace_id) REFERENCES decisions(id) ON DELETE SET NULL;
            END IF;
        END $$;
        CREATE INDEX IF NOT EXISTS ix_execution_step_runs_action_name
            ON execution_step_runs (action_name);
        CREATE INDEX IF NOT EXISTS ix_execution_step_runs_case_id
            ON execution_step_runs (case_id);
        CREATE INDEX IF NOT EXISTS ix_execution_step_runs_decision_trace_id
            ON execution_step_runs (decision_trace_id);
        -- Banking-grade safety: idempotency keys are tenant-globally
        -- unique when present. NULL keys are unconstrained (legacy rows
        -- and read-only steps don't need one).
        CREATE UNIQUE INDEX IF NOT EXISTS uq_execution_step_runs_idempotency_key
            ON execution_step_runs (idempotency_key)
            WHERE idempotency_key IS NOT NULL;
        """
    )

    # ------------------------------------------------------------------
    # 12. graph_edges  — temporal validity + confidence
    # ------------------------------------------------------------------
    op.execute(
        """
        ALTER TABLE graph_edges
            ADD COLUMN IF NOT EXISTS valid_from TIMESTAMPTZ NULL,
            ADD COLUMN IF NOT EXISTS valid_to TIMESTAMPTZ NULL,
            ADD COLUMN IF NOT EXISTS confidence DOUBLE PRECISION NULL;
        CREATE INDEX IF NOT EXISTS ix_graph_edges_valid_window
            ON graph_edges (valid_from, valid_to);
        """
    )


def downgrade() -> None:
    # Reverse order. ALTER TABLE DROP COLUMN IF EXISTS keeps this safe
    # against partial-apply states. Constraints and indexes drop with
    # their parent objects.
    op.execute(
        """
        ALTER TABLE graph_edges
            DROP COLUMN IF EXISTS confidence,
            DROP COLUMN IF EXISTS valid_to,
            DROP COLUMN IF EXISTS valid_from;
        """
    )
    op.execute(
        """
        DROP INDEX IF EXISTS uq_execution_step_runs_idempotency_key;
        ALTER TABLE execution_step_runs
            DROP CONSTRAINT IF EXISTS fk_execution_step_runs_decision_trace,
            DROP CONSTRAINT IF EXISTS fk_execution_step_runs_case,
            DROP COLUMN IF EXISTS decision_trace_id,
            DROP COLUMN IF EXISTS case_id,
            DROP COLUMN IF EXISTS duplicate_check_status,
            DROP COLUMN IF EXISTS idempotency_key,
            DROP COLUMN IF EXISTS executed_by,
            DROP COLUMN IF EXISTS execution_mode,
            DROP COLUMN IF EXISTS action_type,
            DROP COLUMN IF EXISTS action_name;
        """
    )
    op.execute(
        """
        ALTER TABLE approval_requests
            DROP CONSTRAINT IF EXISTS fk_approval_requests_decision_trace,
            DROP CONSTRAINT IF EXISTS fk_approval_requests_case,
            DROP COLUMN IF EXISTS decision_trace_id,
            DROP COLUMN IF EXISTS case_id,
            DROP COLUMN IF EXISTS sod_violation_reason,
            DROP COLUMN IF EXISTS sod_check_status,
            DROP COLUMN IF EXISTS executed_by,
            DROP COLUMN IF EXISTS recommended_by,
            DROP COLUMN IF EXISTS approval_note,
            DROP COLUMN IF EXISTS approval_channel,
            DROP COLUMN IF EXISTS approver_role,
            DROP COLUMN IF EXISTS action_name;
        """
    )
    op.execute(
        """
        ALTER TABLE decision_trace_events
            DROP CONSTRAINT IF EXISTS fk_decision_trace_events_decision,
            DROP COLUMN IF EXISTS tool_output_ref,
            DROP COLUMN IF EXISTS tool_input_ref,
            DROP COLUMN IF EXISTS tool_name,
            DROP COLUMN IF EXISTS decision_id;
        """
    )
    op.execute(
        """
        ALTER TABLE decisions
            DROP COLUMN IF EXISTS policy_result,
            DROP COLUMN IF EXISTS risk_level,
            DROP COLUMN IF EXISTS decision_summary,
            DROP COLUMN IF EXISTS decision_intent;
        """
    )
    op.execute(
        """
        ALTER TABLE evidence_items
            DROP COLUMN IF EXISTS redaction_status,
            DROP COLUMN IF EXISTS source_type,
            DROP COLUMN IF EXISTS collected_by,
            DROP COLUMN IF EXISTS evidence_time;
        """
    )
    op.execute(
        """
        DROP INDEX IF EXISTS uq_resolution_sessions_case_number;
        ALTER TABLE resolution_sessions
            DROP CONSTRAINT IF EXISTS fk_resolution_sessions_agent_entity,
            DROP CONSTRAINT IF EXISTS fk_resolution_sessions_request_entity,
            DROP CONSTRAINT IF EXISTS fk_resolution_sessions_workflow_entity,
            DROP CONSTRAINT IF EXISTS fk_resolution_sessions_user_entity,
            DROP COLUMN IF EXISTS agent_entity_id,
            DROP COLUMN IF EXISTS request_entity_id,
            DROP COLUMN IF EXISTS workflow_entity_id,
            DROP COLUMN IF EXISTS user_entity_id,
            DROP COLUMN IF EXISTS environment,
            DROP COLUMN IF EXISTS severity,
            DROP COLUMN IF EXISTS priority,
            DROP COLUMN IF EXISTS description,
            DROP COLUMN IF EXISTS title,
            DROP COLUMN IF EXISTS issue_type,
            DROP COLUMN IF EXISTS case_type,
            DROP COLUMN IF EXISTS case_number;
        """
    )
    op.execute("DROP TABLE IF EXISTS case_state_transitions;")
    op.execute("DROP TABLE IF EXISTS case_outcomes;")
    op.execute("DROP TABLE IF EXISTS fix_patterns;")
    op.execute("DROP TABLE IF EXISTS error_signatures;")
    op.execute("DROP TABLE IF EXISTS action_policies;")
    op.execute("DROP TABLE IF EXISTS decision_evidence;")
    op.execute("DROP TABLE IF EXISTS claim_evidence;")
    op.execute("DROP TABLE IF EXISTS claims;")
    op.execute("DROP TABLE IF EXISTS entities;")
