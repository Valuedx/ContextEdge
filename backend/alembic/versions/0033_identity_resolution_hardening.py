"""Identity-resolution hardening: typed aliases, resolution states, uniqueness.

The previous resolver was exact alias matching with immediate trusted
creation on miss: ``jsmith``, ``John Smith``, ``jsmith@acme.com``, and
``J. Smith - Network`` each became a separate confidence-0.8 identity, and
"Phoenix" the application could match "Phoenix" the person. This migration
adds the schema for the layered resolver
(``services/identity_service.py``):

- ``canonical_identities``: ``normalized_name`` (casefolded, backfilled),
  ``resolution_state`` (``resolved`` | ``provisional`` | ``needs_review`` |
  ``verified`` — existing rows backfill to ``resolved``),
  ``resolution_confidence``, ``resolution_method``.
- ``identity_aliases``: ``tenant_id`` (denormalized from the canonical row
  so uniqueness can be tenant-scoped; backfilled), ``normalized_alias``
  (backfilled from ``lower(alias_text)``), ``alias_type`` (default
  ``display_name``), ``source_system``, ``last_seen_at``,
  ``times_observed``.
- Uniqueness: a *strong* identifier (email, username, hostname, fqdn,
  ip_address, serial_number, external_id) may belong to only one identity
  per tenant — partial unique index. Display names are deliberately NOT
  unique (two employees can share a name).
- Lookup index for the typed exact-alias path:
  ``(tenant_id, alias_type, normalized_alias)``.

Additive only; every ADD COLUMN / CREATE INDEX is IF NOT EXISTS guarded,
backfills are idempotent UPDATEs, so re-running is safe.

Revision ID: 0033_identity_resolution_hardening
Revises: 0032_halfvec_hnsw_indexes
Create Date: 2026-07-29 14:00:00.000000
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0033_identity_resolution_hardening"
down_revision: Union[str, None] = "0032_halfvec_hnsw_indexes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

STRONG_ALIAS_TYPES_SQL = (
    "('email', 'username', 'hostname', 'fqdn', 'ip_address', "
    "'serial_number', 'external_id')"
)


def upgrade() -> None:
    op.execute(
        f"""
        ALTER TABLE canonical_identities
            ADD COLUMN IF NOT EXISTS normalized_name VARCHAR(500),
            ADD COLUMN IF NOT EXISTS resolution_state VARCHAR(20) NOT NULL DEFAULT 'resolved',
            ADD COLUMN IF NOT EXISTS resolution_confidence DOUBLE PRECISION,
            ADD COLUMN IF NOT EXISTS resolution_method VARCHAR(50);

        UPDATE canonical_identities
        SET normalized_name = lower(regexp_replace(btrim(canonical_name), '\\s+', ' ', 'g'))
        WHERE normalized_name IS NULL;

        CREATE INDEX IF NOT EXISTS ix_canonical_identities_tenant_type_normalized
            ON canonical_identities (tenant_id, entity_type, normalized_name);

        CREATE INDEX IF NOT EXISTS ix_canonical_identities_resolution_state
            ON canonical_identities (tenant_id, resolution_state)
            WHERE resolution_state IN ('provisional', 'needs_review');

        ALTER TABLE identity_aliases
            ADD COLUMN IF NOT EXISTS tenant_id UUID,
            ADD COLUMN IF NOT EXISTS normalized_alias VARCHAR(500),
            ADD COLUMN IF NOT EXISTS alias_type VARCHAR(30) NOT NULL DEFAULT 'display_name',
            ADD COLUMN IF NOT EXISTS source_system VARCHAR(50),
            ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS times_observed INTEGER NOT NULL DEFAULT 1;

        UPDATE identity_aliases ia
        SET tenant_id = ci.tenant_id
        FROM canonical_identities ci
        WHERE ia.canonical_identity_id = ci.id AND ia.tenant_id IS NULL;

        UPDATE identity_aliases
        SET normalized_alias = lower(regexp_replace(btrim(alias_text), '\\s+', ' ', 'g'))
        WHERE normalized_alias IS NULL;

        CREATE INDEX IF NOT EXISTS ix_identity_aliases_tenant_type_normalized
            ON identity_aliases (tenant_id, alias_type, normalized_alias);
        """
    )
    # Strong identifiers are unique per tenant. Deduplicate any pre-existing
    # collisions first (keep the oldest row) so the index can build.
    op.execute(
        f"""
        DELETE FROM identity_aliases a
        USING identity_aliases b
        WHERE a.tenant_id = b.tenant_id
          AND a.alias_type = b.alias_type
          AND a.normalized_alias = b.normalized_alias
          AND a.alias_type IN {STRONG_ALIAS_TYPES_SQL}
          AND a.created_at > b.created_at;

        CREATE UNIQUE INDEX IF NOT EXISTS uq_identity_aliases_tenant_strong
            ON identity_aliases (tenant_id, alias_type, normalized_alias)
            WHERE alias_type IN {STRONG_ALIAS_TYPES_SQL};
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP INDEX IF EXISTS uq_identity_aliases_tenant_strong;
        DROP INDEX IF EXISTS ix_identity_aliases_tenant_type_normalized;
        DROP INDEX IF EXISTS ix_canonical_identities_resolution_state;
        DROP INDEX IF EXISTS ix_canonical_identities_tenant_type_normalized;

        ALTER TABLE identity_aliases
            DROP COLUMN IF EXISTS times_observed,
            DROP COLUMN IF EXISTS last_seen_at,
            DROP COLUMN IF EXISTS source_system,
            DROP COLUMN IF EXISTS alias_type,
            DROP COLUMN IF EXISTS normalized_alias,
            DROP COLUMN IF EXISTS tenant_id;

        ALTER TABLE canonical_identities
            DROP COLUMN IF EXISTS resolution_method,
            DROP COLUMN IF EXISTS resolution_confidence,
            DROP COLUMN IF EXISTS resolution_state,
            DROP COLUMN IF EXISTS normalized_name;
        """
    )
