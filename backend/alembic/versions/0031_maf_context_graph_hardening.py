"""Harden Context Graph ownership, temporal integrity, and MAF reasoning paths.

Revision ID: 0031_maf_context_graph_hardening
Revises: 0030_evidence_chunks
Create Date: 2026-07-28 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0031_maf_context_graph_hardening"
down_revision: str | None = "0030_evidence_chunks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _preflight() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM entities
                GROUP BY tenant_id, entity_type, external_system, external_id
                HAVING COUNT(*) > 1
            ) THEN
                RAISE EXCEPTION
                    '0031 preflight: duplicate entity natural keys exist within a tenant';
            END IF;

            IF EXISTS (
                SELECT 1
                FROM resolution_sessions
                WHERE case_number IS NOT NULL
                GROUP BY tenant_id, case_number
                HAVING COUNT(*) > 1
            ) THEN
                RAISE EXCEPTION
                    '0031 preflight: duplicate case numbers exist within a tenant';
            END IF;

            IF EXISTS (
                SELECT 1
                FROM playbooks
                GROUP BY tenant_id, stable_key
                HAVING COUNT(*) > 1
            ) THEN
                RAISE EXCEPTION
                    '0031 preflight: duplicate playbook stable keys exist within a tenant';
            END IF;

            IF EXISTS (
                SELECT 1
                FROM graph_edges ge
                LEFT JOIN tenants t ON t.id = ge.tenant_id
                WHERE t.id IS NULL
            ) THEN
                RAISE EXCEPTION
                    '0031 preflight: graph_edges contains orphan tenant references';
            END IF;

            IF EXISTS (
                SELECT 1
                FROM graph_edges ge
                LEFT JOIN domains d ON d.id = ge.domain_id
                WHERE ge.domain_id IS NOT NULL AND d.id IS NULL
            ) THEN
                RAISE EXCEPTION
                    '0031 preflight: graph_edges contains orphan domain references';
            END IF;
        END $$;
        """
    )


def _harden_natural_keys() -> None:
    op.execute(
        """
        ALTER TABLE entities
            DROP CONSTRAINT IF EXISTS uq_entities_type_system_external_id;

        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'uq_entities_tenant_type_system_external_id'
            ) THEN
                ALTER TABLE entities
                    ADD CONSTRAINT uq_entities_tenant_type_system_external_id
                    UNIQUE (tenant_id, entity_type, external_system, external_id);
            END IF;
        END $$;

        DROP INDEX IF EXISTS uq_resolution_sessions_case_number;
        ALTER TABLE resolution_sessions
            DROP CONSTRAINT IF EXISTS resolution_sessions_case_number_key;
        CREATE UNIQUE INDEX IF NOT EXISTS uq_resolution_sessions_tenant_case_number
            ON resolution_sessions (tenant_id, case_number)
            WHERE case_number IS NOT NULL;

        ALTER TABLE playbooks
            DROP CONSTRAINT IF EXISTS playbooks_stable_key_key;
        ALTER TABLE playbooks
            DROP CONSTRAINT IF EXISTS uq_playbooks_stable_key;
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'uq_playbooks_tenant_stable_key'
            ) THEN
                ALTER TABLE playbooks
                    ADD CONSTRAINT uq_playbooks_tenant_stable_key
                    UNIQUE (tenant_id, stable_key);
            END IF;
        END $$;
        """
    )


def _add_domain_scope() -> None:
    for table in ("claims", "action_policies", "error_signatures", "fix_patterns"):
        op.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS domain_id UUID NULL;")
        op.execute(
            f"CREATE INDEX IF NOT EXISTS ix_{table}_domain_id ON {table} (domain_id);"
        )
        op.execute(
            f"""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1
                    FROM pg_constraint c
                    JOIN pg_attribute a
                      ON a.attrelid = c.conrelid
                     AND a.attnum = ANY(c.conkey)
                    WHERE c.conrelid = '{table}'::regclass
                      AND c.contype = 'f'
                      AND a.attname = 'domain_id'
                ) THEN
                    ALTER TABLE {table}
                        ADD CONSTRAINT fk_{table}_domain_id
                        FOREIGN KEY (domain_id) REFERENCES domains(id)
                        ON DELETE SET NULL;
                END IF;
            END $$;
            """
        )

    op.execute(
        """
        UPDATE claims c
        SET domain_id = s.domain_id
        FROM resolution_sessions s
        WHERE c.case_id = s.id
          AND c.tenant_id = s.tenant_id
          AND c.domain_id IS NULL;

        UPDATE action_policies ap
        SET domain_id = e.domain_id
        FROM entities e
        WHERE ap.workflow_entity_id = e.id
          AND ap.tenant_id = e.tenant_id
          AND ap.domain_id IS NULL;

        UPDATE error_signatures es
        SET domain_id = p.domain_id
        FROM patterns p
        WHERE es.pattern_id = p.id
          AND es.tenant_id = p.tenant_id
          AND es.domain_id IS NULL;

        UPDATE fix_patterns fp
        SET domain_id = COALESCE(e.domain_id, pb.domain_id, es.domain_id)
        FROM fix_patterns source
        LEFT JOIN entities e
          ON e.id = source.workflow_entity_id
         AND e.tenant_id = source.tenant_id
        LEFT JOIN playbooks pb
          ON pb.id = source.recommended_playbook_id
         AND pb.tenant_id = source.tenant_id
        LEFT JOIN error_signatures es
          ON es.id = source.error_signature_id
         AND es.tenant_id = source.tenant_id
        WHERE fp.id = source.id
          AND fp.domain_id IS NULL;
        """
    )


def _add_association_tables() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS decision_claims (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL REFERENCES tenants(id),
            decision_id UUID NOT NULL REFERENCES decisions(id) ON DELETE CASCADE,
            claim_id UUID NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
            use_type VARCHAR(30) NOT NULL DEFAULT 'supports',
            weight NUMERIC(5,4) NOT NULL DEFAULT 1.0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_decision_claims_decision_claim_use
                UNIQUE (decision_id, claim_id, use_type),
            CONSTRAINT ck_decision_claims_use_type
                CHECK (use_type IN ('supports', 'contradicts', 'risk', 'precondition')),
            CONSTRAINT ck_decision_claims_weight
                CHECK (weight >= 0)
        );
        CREATE INDEX IF NOT EXISTS ix_decision_claims_tenant_id
            ON decision_claims (tenant_id);
        CREATE INDEX IF NOT EXISTS ix_decision_claims_decision_id
            ON decision_claims (decision_id);
        CREATE INDEX IF NOT EXISTS ix_decision_claims_claim_id
            ON decision_claims (claim_id);

        CREATE TABLE IF NOT EXISTS decision_action_policies (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL REFERENCES tenants(id),
            decision_id UUID NOT NULL REFERENCES decisions(id) ON DELETE CASCADE,
            action_policy_id UUID NOT NULL
                REFERENCES action_policies(id) ON DELETE CASCADE,
            policy_result_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_decision_action_policies_decision_policy
                UNIQUE (decision_id, action_policy_id)
        );
        CREATE INDEX IF NOT EXISTS ix_decision_action_policies_tenant_id
            ON decision_action_policies (tenant_id);
        CREATE INDEX IF NOT EXISTS ix_decision_action_policies_decision_id
            ON decision_action_policies (decision_id);
        CREATE INDEX IF NOT EXISTS ix_decision_action_policies_action_policy_id
            ON decision_action_policies (action_policy_id);

        CREATE TABLE IF NOT EXISTS case_outcome_fix_patterns (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL REFERENCES tenants(id),
            case_outcome_id UUID NOT NULL
                REFERENCES case_outcomes(id) ON DELETE CASCADE,
            fix_pattern_id UUID NOT NULL
                REFERENCES fix_patterns(id) ON DELETE CASCADE,
            result VARCHAR(20) NOT NULL,
            confidence NUMERIC(5,4) NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_case_outcome_fix_patterns_outcome_fix_result
                UNIQUE (case_outcome_id, fix_pattern_id, result),
            CONSTRAINT ck_case_outcome_fix_patterns_result
                CHECK (result IN ('successful', 'failed', 'partial')),
            CONSTRAINT ck_case_outcome_fix_patterns_confidence
                CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1))
        );
        CREATE INDEX IF NOT EXISTS ix_case_outcome_fix_patterns_tenant_id
            ON case_outcome_fix_patterns (tenant_id);
        CREATE INDEX IF NOT EXISTS ix_case_outcome_fix_patterns_case_outcome_id
            ON case_outcome_fix_patterns (case_outcome_id);
        CREATE INDEX IF NOT EXISTS ix_case_outcome_fix_patterns_fix_pattern_id
            ON case_outcome_fix_patterns (fix_pattern_id);
        """
    )


def _harden_graph_edges() -> None:
    op.execute(
        """
        UPDATE graph_edges ge
        SET source_node_type = 'entity_term'
        WHERE ge.source_node_type = 'entity'
          AND ge.edge_type = 'involved_in'
          AND NOT EXISTS (
              SELECT 1
              FROM entities e
              WHERE e.id = ge.source_node_id
                AND e.tenant_id = ge.tenant_id
          );

        UPDATE graph_edges
        SET valid_from = COALESCE(valid_from, created_at, NOW())
        WHERE valid_from IS NULL;

        ALTER TABLE graph_edges
            ALTER COLUMN valid_from SET DEFAULT NOW(),
            ALTER COLUMN valid_from SET NOT NULL;

        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM graph_edges
                WHERE valid_to IS NULL
                GROUP BY
                    tenant_id,
                    domain_id,
                    source_node_type,
                    source_node_id,
                    target_node_type,
                    target_node_id,
                    edge_type
                HAVING COUNT(*) > 1
            ) THEN
                RAISE EXCEPTION
                    '0031 preflight: duplicate active logical graph edges exist';
            END IF;

            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conrelid = 'graph_edges'::regclass
                  AND contype = 'f'
                  AND confrelid = 'tenants'::regclass
            ) THEN
                ALTER TABLE graph_edges
                    ADD CONSTRAINT fk_graph_edges_tenant_id
                    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE;
            END IF;

            IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint c
                JOIN pg_attribute a
                  ON a.attrelid = c.conrelid
                 AND a.attnum = ANY(c.conkey)
                WHERE c.conrelid = 'graph_edges'::regclass
                  AND c.contype = 'f'
                  AND a.attname = 'domain_id'
            ) THEN
                ALTER TABLE graph_edges
                    ADD CONSTRAINT fk_graph_edges_domain_id
                    FOREIGN KEY (domain_id) REFERENCES domains(id) ON DELETE CASCADE;
            END IF;

            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'ck_graph_edges_weight_nonnegative'
            ) THEN
                ALTER TABLE graph_edges
                    ADD CONSTRAINT ck_graph_edges_weight_nonnegative CHECK (weight >= 0);
            END IF;

            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'ck_graph_edges_confidence_range'
            ) THEN
                ALTER TABLE graph_edges
                    ADD CONSTRAINT ck_graph_edges_confidence_range
                    CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1));
            END IF;

            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'ck_graph_edges_valid_window'
            ) THEN
                ALTER TABLE graph_edges
                    ADD CONSTRAINT ck_graph_edges_valid_window
                    CHECK (valid_to IS NULL OR valid_to > valid_from);
            END IF;
        END $$;

        CREATE UNIQUE INDEX IF NOT EXISTS uq_graph_edges_active_logical
            ON graph_edges (
                tenant_id,
                domain_id,
                source_node_type,
                source_node_id,
                target_node_type,
                target_node_id,
                edge_type
            ) NULLS NOT DISTINCT
            WHERE valid_to IS NULL;

        CREATE INDEX IF NOT EXISTS ix_graph_edges_current_source
            ON graph_edges (
                tenant_id, source_node_type, source_node_id, domain_id
            )
            WHERE valid_to IS NULL;

        CREATE INDEX IF NOT EXISTS ix_graph_edges_current_target
            ON graph_edges (
                tenant_id, target_node_type, target_node_id, domain_id
            )
            WHERE valid_to IS NULL;

        CREATE INDEX IF NOT EXISTS ix_graph_edges_temporal_source
            ON graph_edges (
                tenant_id, source_node_type, source_node_id, valid_from, valid_to
            );

        CREATE INDEX IF NOT EXISTS ix_graph_edges_temporal_target
            ON graph_edges (
                tenant_id, target_node_type, target_node_id, valid_from, valid_to
            );
        """
    )


def _backfill_relationships() -> None:
    op.execute(
        """
        WITH candidates (
            tenant_id, domain_id,
            source_node_type, source_node_id,
            target_node_type, target_node_id,
            edge_type, weight, confidence, metadata_extra
        ) AS (
            SELECT s.tenant_id, s.domain_id, 'session', s.id, 'entity',
                   refs.target_id, refs.edge_type, 1.0,
                   NULL::numeric, NULL::jsonb
            FROM resolution_sessions s
            CROSS JOIN LATERAL (
                VALUES
                    (s.user_entity_id, 'involves_user'),
                    (s.workflow_entity_id, 'targets_workflow'),
                    (s.request_entity_id, 'tracks_request'),
                    (s.agent_entity_id, 'runs_on_agent')
            ) refs(target_id, edge_type)
            WHERE refs.target_id IS NOT NULL

            UNION ALL
            SELECT er.tenant_id, s.domain_id, 'session', er.session_id,
                   'execution_run', er.id, 'has_execution', 1.0,
                   NULL::numeric, NULL::jsonb
            FROM execution_runs er
            JOIN resolution_sessions s ON s.id = er.session_id
            WHERE er.session_id IS NOT NULL

            UNION ALL
            SELECT er.tenant_id, p.domain_id, 'execution_run', er.id,
                   'playbook', er.playbook_id, 'executes', 1.0, NULL,
                   jsonb_build_object('automation_mode', er.automation_mode)
            FROM execution_runs er
            JOIN playbooks p ON p.id = er.playbook_id

            UNION ALL
            SELECT ar.tenant_id, p.domain_id, 'execution_run', ar.execution_run_id,
                   'approval_request', ar.id, 'requires_approval', 1.0,
                   NULL::numeric, NULL::jsonb
            FROM approval_requests ar
            JOIN execution_runs er ON er.id = ar.execution_run_id
            JOIN playbooks p ON p.id = er.playbook_id

            UNION ALL
            SELECT c.tenant_id, c.domain_id, 'claim', c.id, 'session', c.case_id,
                   'asserted_in', 1.0, c.confidence, NULL::jsonb
            FROM claims c
            WHERE c.case_id IS NOT NULL

            UNION ALL
            SELECT c.tenant_id, c.domain_id, 'claim', ce.claim_id, 'evidence',
                   ce.evidence_id,
                   CASE ce.support_type
                       WHEN 'contradicts' THEN 'contradicted_by'
                       WHEN 'weakens' THEN 'weakened_by'
                       WHEN 'weakened_by' THEN 'weakened_by'
                       ELSE 'supported_by'
                   END,
                   ce.weight, NULL,
                   jsonb_build_object('support_type', ce.support_type)
            FROM claim_evidence ce
            JOIN claims c ON c.id = ce.claim_id

            UNION ALL
            SELECT c.tenant_id, c.domain_id, 'claim', c.id, 'claim',
                   c.superseded_by_claim_id, 'superseded_by', 1.0,
                   c.confidence, NULL::jsonb
            FROM claims c
            WHERE c.superseded_by_claim_id IS NOT NULL

            UNION ALL
            SELECT d.tenant_id, d.domain_id, 'decision', de.decision_id, 'evidence',
                   de.evidence_id, 'based_on', 1.0, d.confidence,
                   jsonb_build_object('support_type', de.support_type)
            FROM decision_evidence de
            JOIN decisions d ON d.id = de.decision_id

            UNION ALL
            SELECT dc.tenant_id, c.domain_id, 'decision', dc.decision_id, 'claim',
                   dc.claim_id, 'supported_by_claim', dc.weight, c.confidence,
                   jsonb_build_object('use_type', dc.use_type)
            FROM decision_claims dc
            JOIN claims c ON c.id = dc.claim_id

            UNION ALL
            SELECT dap.tenant_id, ap.domain_id, 'decision', dap.decision_id,
                   'action_policy', dap.action_policy_id, 'applied_policy',
                   1.0, NULL, dap.policy_result_snapshot
            FROM decision_action_policies dap
            JOIN action_policies ap ON ap.id = dap.action_policy_id

            UNION ALL
            SELECT ap.tenant_id, ap.domain_id, 'action_policy', ap.id, 'entity',
                   ap.workflow_entity_id, 'governs', 1.0,
                   NULL::numeric, NULL::jsonb
            FROM action_policies ap
            WHERE ap.workflow_entity_id IS NOT NULL

            UNION ALL
            SELECT es.tenant_id, es.domain_id, 'error_signature', es.id, 'pattern',
                   es.pattern_id, 'aggregated_by', 1.0,
                   es.confidence, NULL::jsonb
            FROM error_signatures es
            WHERE es.pattern_id IS NOT NULL

            UNION ALL
            SELECT fp.tenant_id, fp.domain_id, 'fix_pattern', fp.id,
                   refs.target_type, refs.target_id, refs.edge_type,
                   1.0, fp.confidence, NULL::jsonb
            FROM fix_patterns fp
            CROSS JOIN LATERAL (
                VALUES
                    ('error_signature', fp.error_signature_id, 'addresses'),
                    ('entity', fp.workflow_entity_id, 'applies_to'),
                    ('playbook', fp.recommended_playbook_id, 'recommends')
            ) refs(target_type, target_id, edge_type)
            WHERE refs.target_id IS NOT NULL

            UNION ALL
            SELECT co.tenant_id, s.domain_id, 'session', co.case_id, 'case_outcome',
                   co.id, 'resulted_in', 1.0, NULL,
                   jsonb_build_object('outcome', co.outcome_status)
            FROM case_outcomes co
            JOIN resolution_sessions s ON s.id = co.case_id

            UNION ALL
            SELECT cofp.tenant_id, fp.domain_id, 'case_outcome',
                   cofp.case_outcome_id, 'fix_pattern', cofp.fix_pattern_id,
                   CASE WHEN cofp.result = 'failed'
                       THEN 'invalidated_fix' ELSE 'validated_fix' END,
                   COALESCE(cofp.confidence, 1.0), cofp.confidence,
                   jsonb_build_object('result', cofp.result)
            FROM case_outcome_fix_patterns cofp
            JOIN fix_patterns fp ON fp.id = cofp.fix_pattern_id
        )
        INSERT INTO graph_edges (
            id, tenant_id, domain_id,
            source_node_type, source_node_id,
            target_node_type, target_node_id,
            edge_type, weight, confidence, metadata_extra,
            created_at, valid_from, valid_to
        )
        SELECT
            md5(random()::text || clock_timestamp()::text)::uuid,
            c.tenant_id, c.domain_id,
            c.source_node_type, c.source_node_id,
            c.target_node_type, c.target_node_id,
            c.edge_type, c.weight, c.confidence, c.metadata_extra,
            NOW(), NOW(), NULL
        FROM candidates c
        WHERE NOT EXISTS (
            SELECT 1
            FROM graph_edges ge
            WHERE ge.tenant_id = c.tenant_id
              AND ge.domain_id IS NOT DISTINCT FROM c.domain_id
              AND ge.source_node_type = c.source_node_type
              AND ge.source_node_id = c.source_node_id
              AND ge.target_node_type = c.target_node_type
              AND ge.target_node_id = c.target_node_id
              AND ge.edge_type = c.edge_type
              AND ge.valid_to IS NULL
        );
        """
    )


def upgrade() -> None:
    _preflight()
    _harden_natural_keys()
    _add_domain_scope()
    _add_association_tables()
    _harden_graph_edges()
    _backfill_relationships()


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE IF EXISTS case_outcome_fix_patterns;
        DROP TABLE IF EXISTS decision_action_policies;
        DROP TABLE IF EXISTS decision_claims;

        DROP INDEX IF EXISTS uq_graph_edges_active_logical;
        DROP INDEX IF EXISTS ix_graph_edges_current_source;
        DROP INDEX IF EXISTS ix_graph_edges_current_target;
        DROP INDEX IF EXISTS ix_graph_edges_temporal_source;
        DROP INDEX IF EXISTS ix_graph_edges_temporal_target;

        ALTER TABLE graph_edges
            DROP CONSTRAINT IF EXISTS ck_graph_edges_valid_window,
            DROP CONSTRAINT IF EXISTS ck_graph_edges_confidence_range,
            DROP CONSTRAINT IF EXISTS ck_graph_edges_weight_nonnegative,
            DROP CONSTRAINT IF EXISTS fk_graph_edges_domain_id,
            DROP CONSTRAINT IF EXISTS fk_graph_edges_tenant_id;
        ALTER TABLE graph_edges
            ALTER COLUMN valid_from DROP NOT NULL,
            ALTER COLUMN valid_from DROP DEFAULT;

        UPDATE graph_edges ge
        SET source_node_type = 'entity'
        WHERE ge.source_node_type = 'entity_term'
          AND ge.edge_type = 'involved_in';

        ALTER TABLE fix_patterns
            DROP CONSTRAINT IF EXISTS fk_fix_patterns_domain_id,
            DROP COLUMN IF EXISTS domain_id;
        ALTER TABLE error_signatures
            DROP CONSTRAINT IF EXISTS fk_error_signatures_domain_id,
            DROP COLUMN IF EXISTS domain_id;
        ALTER TABLE action_policies
            DROP CONSTRAINT IF EXISTS fk_action_policies_domain_id,
            DROP COLUMN IF EXISTS domain_id;
        ALTER TABLE claims
            DROP CONSTRAINT IF EXISTS fk_claims_domain_id,
            DROP COLUMN IF EXISTS domain_id;

        DROP INDEX IF EXISTS uq_resolution_sessions_tenant_case_number;
        CREATE UNIQUE INDEX IF NOT EXISTS uq_resolution_sessions_case_number
            ON resolution_sessions (case_number)
            WHERE case_number IS NOT NULL;

        ALTER TABLE entities
            DROP CONSTRAINT IF EXISTS uq_entities_tenant_type_system_external_id;
        ALTER TABLE entities
            ADD CONSTRAINT uq_entities_type_system_external_id
            UNIQUE (entity_type, external_system, external_id);

        ALTER TABLE playbooks
            DROP CONSTRAINT IF EXISTS uq_playbooks_tenant_stable_key;
        ALTER TABLE playbooks
            ADD CONSTRAINT playbooks_stable_key_key UNIQUE (stable_key);
        """
    )
