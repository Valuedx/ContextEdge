"""MSP dimension + role-based RLS, replacing the bypass GUC.

SupportFlo plan D7/D8/D9/D28. Three things land together because none of
them is safe alone:

1. ``msps`` + ``tenants.msp_id`` — the MSP level and the tenant-to-MSP
   mapping. ``tenant_id`` keeps its meaning and becomes the client.
2. ``msp_id`` denormalised onto every table that carries ``tenant_id``. A
   join inside a security predicate is a performance trap and an extra
   surface for a mistake, so the key the policy tests lives on the row it
   protects.
3. Two roles replacing the bypass GUC. ``0078``'s policy admitted every row
   when ``app.bypass_rls = 'on'`` — an application-controlled string, so
   anything that could execute SQL could turn isolation off. Escalation from
   client to MSP scope now needs different credentials.

**Why the columns are nullable.** A NOT NULL column cannot be added to a
populated table without a default and there is no correct default for "which
MSP owns this row". They are added nullable, backfilled from ``tenants``,
and left nullable so a row inserted by code that predates this migration
fails closed (NULL matches no policy) rather than being rejected at insert
by a constraint the caller cannot satisfy. A later migration can tighten
once every writer sets it.

**Why the roles have no password here.** Migrations are committed to git.
The roles are created NOLOGIN; an operator grants LOGIN and a password out
of band. ``NOBYPASSRLS`` is set explicitly and asserted by
``test_application_roles_do_not_hold_bypassrls`` — a role with BYPASSRLS
defeats the entire mechanism silently.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0097_msp_tenancy_and_role_rls"
down_revision = "0096_clarification_regeneration"
branch_labels = None
depends_on = None

PLATFORM_ROLE = "ce_app_platform"
MSP_ROLE = "ce_app_msp"
CLIENT_ROLE = "ce_app_client"

# Policy bodies. NULLIF makes an unset *or* empty GUC collapse to NULL, and
# `col = NULL` is NULL -> the row is filtered out. Failing closed is the
# default path, not a case someone has to remember to write.
_MSP_PREDICATE = """
  msp_id IS NOT NULL
  AND msp_id = NULLIF(current_setting('app.msp_id', true), '')::uuid
"""
_CLIENT_PREDICATE = """
  msp_id IS NOT NULL
  AND msp_id = NULLIF(current_setting('app.msp_id', true), '')::uuid
  AND tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
"""


def _scoped_tables(conn) -> list[str]:
    """Tables carrying tenant_id, which is what 0078 put policies on."""
    rows = conn.execute(
        sa.text(
            """
            SELECT c.table_name
            FROM information_schema.columns c
            JOIN information_schema.tables t
              ON t.table_schema = c.table_schema AND t.table_name = c.table_name
            WHERE c.table_schema = 'public'
              AND c.column_name = 'tenant_id'
              AND c.table_name <> 'tenants'
              AND t.table_type = 'BASE TABLE'
            ORDER BY c.table_name
            """
        )
    )
    return [r[0] for r in rows]



_FILL_MSP_FN = """
CREATE OR REPLACE FUNCTION ce_fill_msp_id() RETURNS trigger AS $$
DECLARE
  derived uuid;
BEGIN
  -- Derive from the row's own tenant. This is the authoritative direction:
  -- tenants.msp_id is the mapping, so a row can never disagree with it.
  IF NEW.msp_id IS NULL AND NEW.tenant_id IS NOT NULL THEN
    SELECT msp_id INTO derived FROM tenants WHERE id = NEW.tenant_id;
    IF derived IS NOT NULL THEN
      NEW.msp_id := derived;
    END IF;
  END IF;

  -- Fall back to the session scope for the case the lookup cannot serve:
  -- an insert whose tenant row is not yet visible in this transaction.
  IF NEW.msp_id IS NULL THEN
    BEGIN
      NEW.msp_id := NULLIF(current_setting('app.msp_id', true), '')::uuid;
    EXCEPTION WHEN invalid_text_representation THEN
      NEW.msp_id := NULL;
    END;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""


def _has_column(conn, table: str, column: str) -> bool:
    return bool(
        conn.execute(
            sa.text(
                "SELECT 1 FROM information_schema.columns"
                " WHERE table_schema='public' AND table_name=:t AND column_name=:c"
            ),
            {"t": table, "c": column},
        ).first()
    )


def _has_table(conn, table: str) -> bool:
    return bool(
        conn.execute(
            sa.text(
                "SELECT 1 FROM information_schema.tables"
                " WHERE table_schema='public' AND table_name=:t"
            ),
            {"t": table},
        ).first()
    )


def _has_constraint(conn, name: str) -> bool:
    return bool(
        conn.execute(
            sa.text("SELECT 1 FROM pg_constraint WHERE conname=:n"), {"n": name}
        ).first()
    )

def _table_owner(conn, table: str) -> str:
    """The role that owns ``table``, which FORCE RLS binds like any other."""
    row = conn.execute(
        sa.text("SELECT tableowner FROM pg_tables WHERE schemaname='public' AND tablename=:t"),
        {"t": table},
    ).scalar_one()
    return str(row)

def upgrade() -> None:
    conn = op.get_bind()
    uuid_type = postgresql.UUID(as_uuid=True)

    # `0078` left ENABLE + FORCE ROW LEVEL SECURITY and its `tenant_isolation`
    # policy on every scoped table, and FORCE binds the table owner — which is
    # what this migration runs as. Without this line the backfill UPDATEs below
    # match ZERO rows and report success, leaving every msp_id NULL; because the
    # new policies fail closed, that is a silently empty corpus rather than a
    # loud failure. Taking 0078's own escape hatch here is correct precisely
    # because it is the last migration in which that hatch still exists.
    op.execute(sa.text("SELECT set_config('app.bypass_rls', 'on', false)"))

    # --- 1. The MSP level -------------------------------------------------
    # Guarded throughout: `0001` builds the schema with `Base.metadata.create_all`,
    # so on a database built that way these objects already exist from the models.
    # Without the guards this migration cannot run on a fresh database at all.
    if not _has_table(conn, "msps"):
        op.create_table(
            "msps",
            sa.Column("id", uuid_type, primary_key=True),
            sa.Column("name", sa.String(255), nullable=False),
            sa.Column("slug", sa.String(100), nullable=False, unique=True),
            sa.Column("data_residency", sa.String(32), nullable=True),
            sa.Column(
                "config", postgresql.JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False
            ),
            sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
        )
        op.create_index("ix_msps_slug", "msps", ["slug"], unique=True)

    # --- 2. The tenant-to-MSP mapping ------------------------------------
    if not _has_column(conn, "tenants", "msp_id"):
        op.add_column("tenants", sa.Column("msp_id", uuid_type, nullable=True))
        op.create_index("ix_tenants_msp_id", "tenants", ["msp_id"])
    if not _has_constraint(conn, "fk_tenants_msp_id"):
        op.create_foreign_key(
            "fk_tenants_msp_id", "tenants", "msps", ["msp_id"], ["id"], ondelete="CASCADE"
        )

    # Every existing tenant belongs to one default MSP. A deployment that
    # already serves several MSPs cannot be split automatically — nothing in
    # the data says which client belongs to whom — so this puts them in one
    # bucket and the operator re-parents them. That is visible and reversible;
    # guessing would be neither.
    conn.execute(
        sa.text(
            """
            INSERT INTO msps (id, name, slug, config, is_active, created_at, updated_at)
            SELECT gen_random_uuid(), 'Default MSP', 'default-msp', '{}'::jsonb, true,
                   now(), now()
            WHERE EXISTS (SELECT 1 FROM tenants)
              AND NOT EXISTS (SELECT 1 FROM msps WHERE slug = 'default-msp')
            """
        )
    )
    conn.execute(
        sa.text(
            """
            UPDATE tenants
            SET msp_id = (SELECT id FROM msps WHERE slug = 'default-msp')
            WHERE msp_id IS NULL
              AND EXISTS (SELECT 1 FROM msps WHERE slug = 'default-msp')
            """
        )
    )

    # --- 3. msp_id on every scoped table ---------------------------------
    tables = _scoped_tables(conn)
    for table in tables:
        if not _has_column(conn, table, "msp_id"):
            op.add_column(table, sa.Column("msp_id", uuid_type, nullable=True))
            op.create_index(f"ix_{table}_msp_id", table, ["msp_id"])
        # Denormalised from the row's own tenant, which is the only source
        # that cannot disagree with the FK.
        conn.execute(
            sa.text(
                f"UPDATE {table} t SET msp_id = ten.msp_id "  # noqa: S608 - identifiers from catalog
                f"FROM tenants ten WHERE ten.id = t.tenant_id AND t.msp_id IS NULL"
            )
        )
        if not _has_constraint(conn, f"fk_{table}_msp_id"):
            op.create_foreign_key(
                f"fk_{table}_msp_id", table, "msps", ["msp_id"], ["id"], ondelete="CASCADE"
            )

    # --- 3b. A writer for msp_id -----------------------------------------
    # Without this, every row inserted after this migration carries a NULL
    # msp_id, and because the policies fail closed that row is invisible to
    # the tenant that created it. A trigger rather than ORM defaults because
    # 624 predicates and several raw-SQL paths write these tables, and a
    # default only helps the callers that go through the ORM.
    conn.execute(sa.text(_FILL_MSP_FN))
    for table in tables:
        op.execute(sa.text(f"DROP TRIGGER IF EXISTS trg_ce_fill_msp_id ON {table}"))
        op.execute(
            sa.text(
                f"CREATE TRIGGER trg_ce_fill_msp_id BEFORE INSERT OR UPDATE ON {table} "
                "FOR EACH ROW EXECUTE FUNCTION ce_fill_msp_id()"
            )
        )

    # --- 4. Roles ---------------------------------------------------------
    # NOBYPASSRLS is the assertion that matters; NOLOGIN keeps credentials
    # out of git. Idempotent so a re-run on a database that already has them
    # (a shared dev box) does not fail.
    for role in (PLATFORM_ROLE, MSP_ROLE, CLIENT_ROLE):
        conn.execute(
            sa.text(
                f"""
                DO $$
                BEGIN
                  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
                    CREATE ROLE {role} NOLOGIN NOBYPASSRLS;
                  ELSE
                    ALTER ROLE {role} NOBYPASSRLS;
                  END IF;
                END $$;
                """
            )
        )
        conn.execute(sa.text(f"GRANT USAGE ON SCHEMA public TO {role}"))
        conn.execute(
            sa.text(
                "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public "
                f"TO {role}"
            )
        )
        conn.execute(
            sa.text(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {role}")
        )
        conn.execute(
            sa.text(
                "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
                f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {role}"
            )
        )

    # --- 5. Policies ------------------------------------------------------
    # The migration runs as the owner and FORCE RLS binds the owner, which is
    # why the backfill above needed the bypass GUC. From here the old policy is
    # dropped and the owner is admitted by `ce_owner_all` instead — a policy,
    # not a session value, so the replacement is complete rather than partial.
    for table in tables:
        op.execute(sa.text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
        op.execute(sa.text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
        # 0078's single bypassable policy is what this replaces.
        op.execute(sa.text(f"DROP POLICY IF EXISTS tenant_isolation ON {table}"))
        op.execute(sa.text(f"DROP POLICY IF EXISTS ce_msp_isolation ON {table}"))
        op.execute(sa.text(f"DROP POLICY IF EXISTS ce_client_isolation ON {table}"))
        op.execute(sa.text(f"DROP POLICY IF EXISTS ce_owner_all ON {table}"))
        op.execute(sa.text(f"DROP POLICY IF EXISTS ce_platform_all ON {table}"))
        op.execute(
            sa.text(
                f"CREATE POLICY ce_msp_isolation ON {table} TO {MSP_ROLE} "
                f"USING ({_MSP_PREDICATE}) WITH CHECK ({_MSP_PREDICATE})"
            )
        )
        op.execute(
            sa.text(
                f"CREATE POLICY ce_client_isolation ON {table} TO {CLIENT_ROLE} "
                f"USING ({_CLIENT_PREDICATE}) WITH CHECK ({_CLIENT_PREDICATE})"
            )
        )
        # Platform tier: the third level of Platform -> MSP -> Client. A named
        # grantable role, not an implicit one, so "who can see everything" is a
        # credential someone holds rather than a condition code can satisfy.
        op.execute(
            sa.text(
                f"CREATE POLICY ce_platform_all ON {table} TO {PLATFORM_ROLE} "
                "USING (true) WITH CHECK (true)"
            )
        )
        # The table owner is bound by FORCE RLS and still has to run migrations,
        # backups and restores, so it needs a policy of its own. Resolved from
        # pg_class rather than written as CURRENT_USER: CURRENT_USER is
        # evaluated at DDL time, so running this migration as a superuser that
        # is not the owner would name the superuser (who bypasses RLS anyway)
        # and lock the real owner out of its own tables.
        op.execute(
            sa.text(
                f'CREATE POLICY ce_owner_all ON {table} TO "{_table_owner(conn, table)}" '
                "USING (true) WITH CHECK (true)"
            )
        )


    # --- 6. The hierarchy tables themselves --------------------------------
    # `msps` has no tenant_id and `tenants` is excluded from the loop above, so
    # neither is reached by the per-table policies — while the GRANTs below
    # cover ALL TABLES. Measured before this block: a client scoped to one MSP
    # could read every row of both, i.e. the name and slug of every MSP on the
    # platform and every client of every MSP. The two tables that DEFINE the
    # boundary were the two without one.
    #
    # `msps`: you may see your own MSP, never a sibling.
    # `tenants`: an MSP sees its own clients; a client sees only itself.
    op.execute(sa.text("ALTER TABLE msps ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text("ALTER TABLE msps FORCE ROW LEVEL SECURITY"))
    op.execute(sa.text("ALTER TABLE tenants ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text("ALTER TABLE tenants FORCE ROW LEVEL SECURITY"))

    msp_self = """
      id = NULLIF(current_setting('app.msp_id', true), '')::uuid
    """
    tenant_of_msp = """
      msp_id IS NOT NULL
      AND msp_id = NULLIF(current_setting('app.msp_id', true), '')::uuid
    """
    tenant_self = tenant_of_msp + """
      AND id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
    """

    for table, msp_pred, client_pred in (
        ("msps", msp_self, msp_self),
        ("tenants", tenant_of_msp, tenant_self),
    ):
        for name in (
            "ce_msp_isolation",
            "ce_client_isolation",
            "ce_platform_all",
            "ce_owner_all",
        ):
            op.execute(sa.text(f"DROP POLICY IF EXISTS {name} ON {table}"))
        op.execute(
            sa.text(
                f"CREATE POLICY ce_msp_isolation ON {table} TO {MSP_ROLE} "
                f"USING ({msp_pred}) WITH CHECK ({msp_pred})"
            )
        )
        op.execute(
            sa.text(
                f"CREATE POLICY ce_client_isolation ON {table} TO {CLIENT_ROLE} "
                f"USING ({client_pred}) WITH CHECK ({client_pred})"
            )
        )
        op.execute(
            sa.text(
                f"CREATE POLICY ce_platform_all ON {table} TO {PLATFORM_ROLE} "
                "USING (true) WITH CHECK (true)"
            )
        )
        op.execute(
            sa.text(
                f'CREATE POLICY ce_owner_all ON {table} TO "{_table_owner(conn, table)}" '
                "USING (true) WITH CHECK (true)"
            )
        )


def downgrade() -> None:
    conn = op.get_bind()
    tables = _scoped_tables(conn)

    for table in tables:
        op.execute(sa.text(f"DROP TRIGGER IF EXISTS trg_ce_fill_msp_id ON {table}"))
        op.execute(sa.text(f"DROP POLICY IF EXISTS ce_msp_isolation ON {table}"))
        op.execute(sa.text(f"DROP POLICY IF EXISTS ce_client_isolation ON {table}"))
        op.execute(sa.text(f"DROP POLICY IF EXISTS ce_owner_all ON {table}"))
        op.execute(sa.text(f"DROP POLICY IF EXISTS ce_platform_all ON {table}"))
        # Restore 0078's policy so a downgrade is not a silent lockout.
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
        op.drop_constraint(f"fk_{table}_msp_id", table, type_="foreignkey")
        op.drop_index(f"ix_{table}_msp_id", table_name=table)
        op.drop_column(table, "msp_id")

    for role in (PLATFORM_ROLE, MSP_ROLE, CLIENT_ROLE):
        conn.execute(
            sa.text(
                "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
                f"REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM {role}"
            )
        )
        conn.execute(
            sa.text(f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {role}")
        )
        conn.execute(sa.text(f"REVOKE ALL ON SCHEMA public FROM {role}"))
        conn.execute(sa.text(f"DROP ROLE IF EXISTS {role}"))

    conn.execute(sa.text("DROP FUNCTION IF EXISTS ce_fill_msp_id()"))
    for table in ("msps", "tenants"):
        for name in (
            "ce_msp_isolation",
            "ce_client_isolation",
            "ce_platform_all",
            "ce_owner_all",
        ):
            op.execute(sa.text(f"DROP POLICY IF EXISTS {name} ON {table}"))
        op.execute(sa.text(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY"))
        op.execute(sa.text(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY"))

    op.drop_index("ix_tenants_msp_id", table_name="tenants")
    op.drop_constraint("fk_tenants_msp_id", "tenants", type_="foreignkey")
    op.drop_column("tenants", "msp_id")
    op.drop_index("ix_msps_slug", table_name="msps")
    op.drop_table("msps")
