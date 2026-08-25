"""Add unique login usernames and rename the default tenant to AutomationEdge.

Login is username-only (no @). Known bootstrap accounts are remapped:

- superadmin-contextedge  (platform super admin)
- tenantadmin-ae          (AutomationEdge tenant admin; ae = AutomationEdge)
- analyst-ae              (analyst)

Existing password hashes are kept. Email is optional and no longer used to sign in.
"""

from alembic import op
import sqlalchemy as sa


revision = "0075_user_username_login"
down_revision = "0074_operational_situations"
branch_labels = None
depends_on = None


_KNOWN_EMAILS = (
    (("admin@contextedge.local", "admin@automationedge.com"), "superadmin-contextedge"),
    (("tenantadmin@contextedge.local", "tenantadmin@automationedge.com"), "tenantadmin-ae"),
    (("analyst@contextedge.local", "analyst@automationedge.com"), "analyst-ae"),
)

_ROLE_USERNAMES = (
    ("platform_super_admin", "superadmin-contextedge"),
    ("tenant_admin", "tenantadmin-ae"),
    ("analyst", "analyst-ae"),
)


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    user_cols = {c["name"] for c in insp.get_columns("users")}
    if "username" not in user_cols:
        op.add_column("users", sa.Column("username", sa.String(length=64), nullable=True))

    conn = bind

    conn.execute(
        sa.text(
            """
            UPDATE tenants
            SET name = 'AutomationEdge'
            WHERE slug = 'automationedge'
            """
        )
    )
    conn.execute(
        sa.text(
            """
            UPDATE tenants
            SET name = 'AutomationEdge', slug = 'automationedge'
            WHERE slug = 'default'
              AND NOT EXISTS (SELECT 1 FROM tenants t2 WHERE t2.slug = 'automationedge')
            """
        )
    )

    for emails, username in _KNOWN_EMAILS:
        for email in emails:
            conn.execute(
                sa.text(
                    """
                    UPDATE users
                    SET username = :username
                    WHERE username IS NULL
                      AND lower(email) = :email
                    """
                ),
                {"username": username, "email": email},
            )

    for role, username in _ROLE_USERNAMES:
        conn.execute(
            sa.text(
                """
                UPDATE users
                SET username = :username
                WHERE id = (
                    SELECT u.id
                    FROM users u
                    JOIN role_bindings rb ON rb.user_id = u.id
                    WHERE rb.role = :role
                      AND u.username IS NULL
                    ORDER BY u.created_at ASC
                    LIMIT 1
                )
                """
            ),
            {"username": username, "role": role},
        )

    leftovers = conn.execute(
        sa.text("SELECT id, email FROM users WHERE username IS NULL")
    ).fetchall()
    taken = {
        row[0]
        for row in conn.execute(sa.text("SELECT username FROM users WHERE username IS NOT NULL")).fetchall()
    }
    for user_id, email in leftovers:
        base = (email or "").split("@", 1)[0].lower()
        cleaned = "".join(ch if ch in "abcdefghijklmnopqrstuvwxyz0123456789._-" else "-" for ch in base)
        cleaned = cleaned.strip(".-") or "user"
        if cleaned[0] not in "abcdefghijklmnopqrstuvwxyz0123456789":
            cleaned = f"u-{cleaned}"
        candidate = cleaned[:64]
        n = 2
        while candidate in taken:
            suffix = f"-{n}"
            candidate = f"{cleaned[: 64 - len(suffix)]}{suffix}"
            n += 1
        taken.add(candidate)
        conn.execute(
            sa.text("UPDATE users SET username = :username WHERE id = :id"),
            {"username": candidate, "id": user_id},
        )

    op.alter_column("users", "username", existing_type=sa.String(length=64), nullable=False)
    existing_indexes = {idx["name"] for idx in sa.inspect(bind).get_indexes("users")}
    if "ix_users_username" not in existing_indexes:
        op.create_index("ix_users_username", "users", ["username"], unique=True)
    op.alter_column("users", "email", existing_type=sa.String(length=320), nullable=True)


def downgrade() -> None:
    op.alter_column("users", "email", existing_type=sa.String(length=320), nullable=False)
    op.drop_index("ix_users_username", table_name="users")
    op.drop_column("users", "username")
