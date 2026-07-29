"""Safety guard for destructive demo/reset scripts.

`reset_db_and_seed` and `demo_maf_seed` TRUNCATE shared tables, which is
tenant-global: it destroys every tenant's patterns, playbooks, episodes, and
evidence on whatever database ``DATABASE_URL`` points at. This guard makes
that impossible to trigger accidentally against a non-development database.
"""

from __future__ import annotations

import os
from urllib.parse import urlsplit

import structlog

from contextedge.config import settings

logger = structlog.get_logger()

_OVERRIDE_ENV_VAR = "CONTEXTEDGE_ALLOW_DB_RESET"


class DestructiveResetBlocked(RuntimeError):
    """Raised when a destructive seed script is run outside development."""


def database_host() -> str:
    """Best-effort hostname of the configured database, for operator display."""
    try:
        return urlsplit(settings.database_url).hostname or "<unknown>"
    except (ValueError, AttributeError):
        return "<unparseable>"


def require_destructive_reset_allowed(script_name: str) -> None:
    """Refuse to run a destructive, tenant-global reset outside development.

    Allowed when ``APP_ENV=development`` (the default local setup) or when the
    operator explicitly sets ``CONTEXTEDGE_ALLOW_DB_RESET=1`` for this run.
    Anything else raises ``DestructiveResetBlocked`` before a single row is
    touched.
    """
    host = database_host()
    if settings.app_env == "development" or os.environ.get(_OVERRIDE_ENV_VAR) == "1":
        logger.warning(
            "seed.destructive_reset_allowed",
            script=script_name,
            app_env=settings.app_env,
            db_host=host,
        )
        print(f"[{script_name}] destructive reset proceeding (app_env={settings.app_env}, db_host={host})")
        return
    raise DestructiveResetBlocked(
        f"{script_name} TRUNCATEs shared tables for ALL tenants and is blocked "
        f"because app_env={settings.app_env!r} (db_host={host}). If you are "
        f"certain this database may be wiped, re-run with {_OVERRIDE_ENV_VAR}=1."
    )
