"""Make a running sync controllable.

A backfill can spend a quarter of an hour inside one `connector.backfill()`
call — the live Zoho corpus measured 913 seconds for a page walk plus 1,855
sequential detail fetches, writing nothing until it returned. There was no way
to pause it, no way to stop it, and no way to tell from outside whether it was
working or hung.

`sync_runs.control` is the signal the running job reads: `pause` or `cancel`,
set by an operator through the API and consulted by the connector inside its
own loops (every page, and every 25 records of the detail fetch) so a stop
lands in seconds rather than at the end.

**Both stops keep what was already fetched.** Nine hundred seconds of API
calls must not be discarded because somebody clicked pause: the records
collected so far are persisted with their checkpoint, so a resume continues
instead of restarting.

`celery_task_id` is the escape hatch — a run whose worker is wedged past
answering a cooperative check can still be revoked.

Revision ID: 0069_sync_run_control
Revises: 0068_case_state_and_source_facets
"""

from alembic import op

revision = "0069_sync_run_control"
down_revision = "0068_case_state_and_source_facets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE sync_runs
            ADD COLUMN IF NOT EXISTS control VARCHAR(20) NULL,
            ADD COLUMN IF NOT EXISTS celery_task_id VARCHAR(120) NULL;
        """
    )
    # The running job polls this per page; the index keeps that a lookup
    # rather than a scan of every run this tenant has ever done.
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_sync_runs_active
            ON sync_runs (source_object_id, status)
            WHERE status = 'running';
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_sync_runs_active;")
    op.execute(
        """
        ALTER TABLE sync_runs
            DROP COLUMN IF EXISTS celery_task_id,
            DROP COLUMN IF EXISTS control;
        """
    )
