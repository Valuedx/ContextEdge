"""A database older than Alembic 1.10 cannot record this chain's revision ids.

Alembic sizes `alembic_version.version_num` when it first creates the table
and never revisits it. Before 1.10 that was `VARCHAR(32)`; today it is
`VARCHAR(255)`. Six revision ids here are longer than 32 characters, so a
long-lived database fails on the *stamp* rather than the DDL:

    DataError: value too long for type character varying(32)

Fresh installs never see it, which is exactly why it survived: every test
environment and every new deployment is created by a current Alembic.
"""

import ast
import pathlib
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from contextedge.migration_support import (
    ALEMBIC_VERSION_WIDTH,
    alembic_version_width,
    widen_alembic_version_column,
)

_VERSIONS = pathlib.Path(__file__).resolve().parents[1] / "alembic" / "versions"


def _revision_ids() -> list[str]:
    ids = []
    for path in sorted(_VERSIONS.glob("*.py")):
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            target = None
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
                target = node.targets[0]
            elif isinstance(node, ast.AnnAssign) and isinstance(node.value, ast.Constant):
                target = node.target
            if isinstance(target, ast.Name) and target.id == "revision":
                ids.append(str(node.value.value))
    return ids


def _connection(width):
    """A connection whose inspector reports one version_num of `width`."""
    connection = MagicMock()
    inspector = SimpleNamespace(
        get_table_names=lambda: ["alembic_version", "tenants"],
        get_columns=lambda _table: [
            {"name": "version_num", "type": SimpleNamespace(length=width)}
        ],
    )
    return connection, inspector


def test_the_chain_still_needs_a_widened_column():
    """If this ever fails because every id got short, the widening can go —
    but shortening an id a deployed database is stamped with turns "stuck"
    into "Can't locate revision", so nobody should do that on purpose."""
    too_long = [r for r in _revision_ids() if len(r) > 32]
    assert too_long, "no revision exceeds 32 chars — re-check whether env.py still needs this"
    assert max(len(r) for r in _revision_ids()) <= ALEMBIC_VERSION_WIDTH


def test_a_legacy_column_is_widened():
    connection, inspector = _connection(32)
    with patch("contextedge.migration_support.inspect", return_value=inspector):
        assert widen_alembic_version_column(connection) is True
    statement = str(connection.execute.call_args.args[0])
    assert "ALTER TABLE alembic_version" in statement
    assert f"VARCHAR({ALEMBIC_VERSION_WIDTH})" in statement


def test_a_current_column_is_left_alone():
    """Idempotent: this runs before every upgrade, on every environment."""
    connection, inspector = _connection(ALEMBIC_VERSION_WIDTH)
    with patch("contextedge.migration_support.inspect", return_value=inspector):
        assert widen_alembic_version_column(connection) is False
    connection.execute.assert_not_called()


def test_a_wider_column_is_never_narrowed():
    connection, inspector = _connection(512)
    with patch("contextedge.migration_support.inspect", return_value=inspector):
        assert widen_alembic_version_column(connection) is False
    connection.execute.assert_not_called()


def test_a_database_with_no_version_table_is_left_to_alembic():
    """A fresh database has no table yet, and Alembic creates it at the
    current width — reaching in first would be inventing state."""
    connection = MagicMock()
    inspector = SimpleNamespace(get_table_names=lambda: ["tenants"], get_columns=lambda _t: [])
    with patch("contextedge.migration_support.inspect", return_value=inspector):
        assert alembic_version_width(connection) is None
        assert widen_alembic_version_column(connection) is False
    connection.execute.assert_not_called()


def test_env_runs_the_widening_before_migrations():
    """The order is the whole point: after `run_migrations` it is too late,
    because the failure happens on the first stamp."""
    source = (_VERSIONS.parent / "env.py").read_text(encoding="utf-8")
    # Scoped to the online path: offline mode has no connection to alter, and
    # its own `run_migrations()` call comes first in the file.
    online = source[source.index("def run_migrations_online"):]
    assert "widen_alembic_version_column(connection)" in online
    assert online.index("widen_alembic_version_column(connection)") < online.index(
        "context.run_migrations()"
    )
