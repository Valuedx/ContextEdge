"""Every ORM column must exist in some migration.

This bug has now shipped three times. `0034_execution_run_updated_at` and
`0049_evidence_chunks_updated_at` both exist for one reason: a model inherits
`TimestampMixin` (via `TenantScopedMixin`) and its `CREATE TABLE` migration
lists the columns the author typed, not the ones the mixin adds. Nothing caught
it, because the backend suite runs without a live Postgres — SQLAlchemy is
happy to *describe* a column the database does not have, and the failure only
appears the first time a real query names it:

    UndefinedColumn: column trust_profiles.updated_at does not exist

...which is every SELECT of that model, in production, on a table that passed
every test.

So this test reads the migration chain as text and asks one question per
column: does any migration create it? It deliberately does **not** check the
other direction — a column that exists in the database but not in the ORM is a
leftover, not an outage.
"""

from __future__ import annotations

import ast
import pathlib
import re

import contextedge.models  # noqa: F401  (registers every mapper)
from contextedge.models.base import Base

_VERSIONS = pathlib.Path(__file__).resolve().parents[1] / "alembic" / "versions"

# Raw-SQL column adds. One statement can add several columns, so the table is
# matched first and every ADD COLUMN inside that statement is collected.
_ALTER_TABLE_SQL = re.compile(
    r"ALTER\s+TABLE\s+(?:ONLY\s+)?(\w+)([^;]*);", re.IGNORECASE
)
_ADD_COLUMN_SQL = re.compile(
    r"ADD\s+COLUMN\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)", re.IGNORECASE
)
# Raw-SQL table creation: CREATE TABLE [IF NOT EXISTS] name ( ... )
_CREATE_TABLE_SQL = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)\s*\((.*?)\n\s*\)\s*;",
    re.IGNORECASE | re.DOTALL,
)
# A column definition line inside one of those: leading identifier, then a type.
_SQL_COLUMN_LINE = re.compile(r"^\s*(\w+)\s+[A-Za-z]", re.MULTILINE)
_SQL_NOT_A_COLUMN = {
    "primary", "unique", "constraint", "foreign", "check", "exclude", "like",
}


# Columns a migration adds through an f-string over a list of tables, which
# this text scan cannot resolve without interpreting the loop. Each is real —
# named here with the migration that adds it — rather than making the scanner
# a small Python interpreter.
_ADDED_DYNAMICALLY: dict[tuple[str, str], str] = {
    ("action_policies", "domain_id"): "0031 (loop over the tables gaining domain scope)",
    ("claims", "domain_id"): "0031 (same loop)",
    ("error_signatures", "domain_id"): "0031 (same loop)",
    ("fix_patterns", "domain_id"): "0031 (same loop)",
    ("entities", "manufacturer"): "0043 (loop over the normalized trait columns)",
    ("entities", "model"): "0043 (same loop)",
    ("entities", "os_name"): "0043 (same loop)",
    ("entities", "os_version"): "0043 (same loop)",
}


def _migration_columns() -> dict[str, set[str]]:
    """``{table: {column, ...}}`` over every migration, both styles."""
    columns: dict[str, set[str]] = {}
    created: dict[str, set[str]] = {}

    def record(table: str, column: str) -> None:
        columns.setdefault(table, set()).add(column)

    def record_created(table: str, column: str) -> None:
        created.setdefault(table, set()).add(column)
        record(table, column)

    for path in sorted(_VERSIONS.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            name = node.func.attr
            if name == "create_table" and node.args and isinstance(node.args[0], ast.Constant):
                table = node.args[0].value
                created.setdefault(table, set())
                columns.setdefault(table, set())
                for arg in node.args[1:]:
                    if (
                        isinstance(arg, ast.Call)
                        and isinstance(arg.func, ast.Attribute)
                        and arg.func.attr == "Column"
                        and arg.args
                        and isinstance(arg.args[0], ast.Constant)
                    ):
                        record_created(table, arg.args[0].value)
            elif (
                name == "add_column"
                and len(node.args) >= 2
                and isinstance(node.args[0], ast.Constant)
            ):
                column = node.args[1]
                if isinstance(column, ast.Call) and column.args and isinstance(
                    column.args[0], ast.Constant
                ):
                    record(node.args[0].value, column.args[0].value)

        # Raw SQL — several early migrations create their tables with op.execute.
        for table, body in _ALTER_TABLE_SQL.findall(source):
            for name in _ADD_COLUMN_SQL.findall(body):
                record(table, name)
        for table, body in _CREATE_TABLE_SQL.findall(source):
            created.setdefault(table, set())
            columns.setdefault(table, set())
            for candidate in _SQL_COLUMN_LINE.findall(body):
                if candidate.lower() not in _SQL_NOT_A_COLUMN:
                    record_created(table, candidate)

    return created, columns


def test_every_orm_column_is_created_by_a_migration():
    """A model column no migration creates is an outage waiting for traffic."""
    created, known_columns = _migration_columns()
    problems: list[str] = []

    for table_name, table in sorted(Base.metadata.tables.items()):
        if table_name not in created:
            # Created by `0001`'s `Base.metadata.create_all`, which builds
            # whatever the models declare at the time it runs — there is no
            # column list to compare against, and a table added to the models
            # without a migration is caught by the test below instead.
            continue
        missing = sorted(
            c.name
            for c in table.columns
            if c.name not in known_columns[table_name]
            and (table_name, c.name) not in _ADDED_DYNAMICALLY
        )
        if missing:
            problems.append(f"{table_name}: {missing}")

    assert not problems, (
        "ORM columns with no CREATE/ALTER in alembic/versions — every query "
        "naming one fails against a database built by the migration chain:\n  "
        + "\n  ".join(problems)
        + "\n\nUsually TimestampMixin: `created_at`/`updated_at` arrive with "
        "TenantScopedMixin and are easy to leave out of a hand-written "
        "create_table. Add them in a NEW migration — editing a shipped one "
        "does nothing for a database already stamped past it."
    )


def test_a_new_table_arrives_with_its_own_migration():
    """`0001` builds the pre-Alembic tables from metadata; everything since
    has to say so in a migration, or it exists only on fresh installs."""
    created, _ = _migration_columns()
    # The tables `0001` created from metadata, recorded once so a NEW table
    # cannot quietly join them.
    legacy = {
        "attachment_artifacts", "audit_logs", "canonical_identities",
        "contradictions", "correlation_edges", "domains", "episode_steps",
        "episodes", "evaluation_datasets", "evaluation_runs", "evidence_items",
        "graph_edges", "identity_aliases", "negative_knowledge_items",
        "pattern_evidence_links", "patterns", "playbook_approvals",
        "playbook_evidence_links", "playbook_versions", "playbooks",
        "raw_evidence_objects", "retrieval_feedback", "role_bindings",
        "source_credentials", "source_objects", "sources", "sync_checkpoints",
        "sync_runs", "tenants", "threads", "users", "workspaces",
    }
    unexplained = sorted(set(Base.metadata.tables) - set(created) - legacy)
    assert not unexplained, (
        "Mapped tables with no create_table in alembic/versions — they exist "
        f"on a fresh install and nowhere else: {unexplained}"
    )
