import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from contextedge.models.base import Base  # noqa: E402

# Import all model modules so Base.metadata is complete for autogenerate / migrations.
import contextedge.models.audit  # noqa: E402, F401
import contextedge.models.events  # noqa: E402, F401
import contextedge.models.episode  # noqa: E402, F401
import contextedge.models.evaluation  # noqa: E402, F401
import contextedge.models.evidence  # noqa: E402, F401
import contextedge.models.pattern  # noqa: E402, F401
import contextedge.models.playbook  # noqa: E402, F401
import contextedge.models.session  # noqa: E402, F401
import contextedge.models.execution  # noqa: E402, F401
import contextedge.models.source  # noqa: E402, F401
import contextedge.models.tenant  # noqa: E402, F401
import contextedge.models.policy  # noqa: E402, F401
import contextedge.models.entity  # noqa: E402, F401
import contextedge.models.claim  # noqa: E402, F401
import contextedge.models.action_policy  # noqa: E402, F401
import contextedge.models.error_signature  # noqa: E402, F401
import contextedge.models.case_outcome  # noqa: E402, F401

from contextedge.config import settings  # noqa: E402
from contextedge.migration_support import widen_alembic_version_column  # noqa: E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# Use the synchronous database URL from settings
db_url = settings.database_url_sync
if db_url:
    config.set_main_option("sqlalchemy.url", db_url)


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        # Before anything else: a version table created by an Alembic older
        # than 1.10 is VARCHAR(32), and six revision ids in this chain are
        # longer than that. Such a database dies mid-chain on the stamp, not
        # on the DDL, which reads like a broken migration rather than a table
        # that predates it. Fresh databases already get 255 and skip this.
        if widen_alembic_version_column(connection):
            connection.commit()
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
