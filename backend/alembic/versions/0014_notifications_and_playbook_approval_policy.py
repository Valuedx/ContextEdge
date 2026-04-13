"""Add persisted notifications and playbook approval policy assignment.

Revision ID: 0014_notifications_and_playbook_approval_policy
Revises: 0013_attachment_processing
Create Date: 2026-04-13 15:10:00.000000
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0014_notifications_and_playbook_approval_policy"
down_revision: Union[str, None] = "0013_attachment_processing"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS notifications (
            id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL REFERENCES tenants(id),
            user_id UUID NULL REFERENCES users(id),
            notification_type VARCHAR(80) NOT NULL,
            title VARCHAR(500) NOT NULL,
            body TEXT NOT NULL,
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            is_read BOOLEAN NOT NULL DEFAULT FALSE,
            read_at TIMESTAMPTZ NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS ix_notifications_tenant_id ON notifications (tenant_id);
        CREATE INDEX IF NOT EXISTS ix_notifications_user_id ON notifications (user_id);
        CREATE INDEX IF NOT EXISTS ix_notifications_notification_type ON notifications (notification_type);
        CREATE INDEX IF NOT EXISTS ix_notifications_is_read ON notifications (is_read);
        ALTER TABLE playbooks
            ADD COLUMN IF NOT EXISTS approval_policy_id UUID NULL REFERENCES tenant_policies(id) ON DELETE SET NULL;
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE playbooks DROP COLUMN IF EXISTS approval_policy_id;")
    op.execute("DROP INDEX IF EXISTS ix_notifications_is_read;")
    op.execute("DROP INDEX IF EXISTS ix_notifications_notification_type;")
    op.execute("DROP INDEX IF EXISTS ix_notifications_user_id;")
    op.execute("DROP INDEX IF EXISTS ix_notifications_tenant_id;")
    op.execute("DROP TABLE IF EXISTS notifications;")
