"""Schema housekeeping that has to happen before any migration runs.

Alembic creates its own `alembic_version` table the first time a database is
stamped, and the width of `version_num` is whatever the Alembic of that day
used. Versions before 1.10 used `VARCHAR(32)`; current Alembic uses
`VARCHAR(255)`. The column is never widened afterwards, because no migration
owns it.

Six revision ids in this chain are longer than 32 characters
(`0014_notifications_and_playbook_approval_policy` is 47). A database created
by a modern Alembic runs the whole chain; a database whose version table was
created years ago fails the moment it reaches one of them:

    DataError: value too long for type character varying(32)
    [SQL: UPDATE alembic_version SET version_num='0056_policy_versioning_and_checks' ...]

The DDL rolls back with it, so the database is not corrupted — it is simply
stuck, and stuck in a way that reads like a broken migration rather than a
table that predates it.

Renaming the six revisions would be the wrong fix: an id is only meaningful
while it matches what a deployed database is stamped with, and shortening one
that some environment already recorded turns "stuck" into "Can't locate
revision". Widening the column costs nothing and matches what a fresh install
already gets.
"""

from __future__ import annotations

import structlog
from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection

logger = structlog.get_logger()

# What current Alembic creates. Matching it exactly means an upgraded
# database and a fresh one end up with the same schema.
ALEMBIC_VERSION_WIDTH = 255
_VERSION_TABLE = "alembic_version"
_VERSION_COLUMN = "version_num"


def alembic_version_width(connection: Connection) -> int | None:
    """Current width of `alembic_version.version_num`, or None if unknown.

    None covers both "no version table yet" (a fresh database — Alembic will
    create it at the right width) and a dialect that does not report a length.
    """
    inspector = inspect(connection)
    if _VERSION_TABLE not in set(inspector.get_table_names()):
        return None
    for column in inspector.get_columns(_VERSION_TABLE):
        if column["name"] == _VERSION_COLUMN:
            return getattr(column["type"], "length", None)
    return None


def widen_alembic_version_column(connection: Connection) -> bool:
    """Widen a legacy version table in place. True if it was altered.

    Idempotent and safe to call on every upgrade: Postgres widens a varchar
    without rewriting the table, and a column already at or above the target
    is left alone.
    """
    width = alembic_version_width(connection)
    if width is None or width >= ALEMBIC_VERSION_WIDTH:
        return False
    connection.execute(
        text(
            f"ALTER TABLE {_VERSION_TABLE} "
            f"ALTER COLUMN {_VERSION_COLUMN} TYPE VARCHAR({ALEMBIC_VERSION_WIDTH})"
        )
    )
    logger.info(
        "alembic_version.widened",
        from_width=width,
        to_width=ALEMBIC_VERSION_WIDTH,
        reason="revision ids in this chain exceed the legacy 32-character column",
    )
    return True
