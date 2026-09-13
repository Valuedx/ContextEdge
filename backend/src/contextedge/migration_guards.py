"""Make schema-creating migration operations skip what already exists.

**The problem this exists for.** `0001` builds the schema with
`Base.metadata.create_all`, which reflects whatever the models declare *today*
rather than what they declared at `0001`. Every later migration that creates
something still present in the models therefore runs into an object that
`0001` already made. Measured on a fresh PostgreSQL: **17 of 96 migrations
fail**, the first at `0070` (`episodes.ai_review`), all of them
`DuplicateColumn`, `DuplicateTable` or `DuplicateObject`.

The consequence is larger than the symptom. The migration chain cannot build
a database from scratch, so nothing can be tested against one — which is why
this repo has 2 integration tests and enforces schema governance by scanning
migration *text* instead of running it.

**Why guards rather than fixing the 17.** Editing them means 17 diffs in
different styles, from a single `add_column` to 26 operations in `0094`, each
a chance to guard the wrong thing. This is one file to review, applies the
same rule everywhere, and leaves shipped migrations untouched — which matters
because their content is what a stamped database is a record of.

**Why this is safe on a real upgrade.** A guard only changes behaviour when
the object already exists. On the intended path it does not, so the guard
never fires and the migration runs exactly as before. A database already
stamped past these revisions never re-runs them at all.

**What it costs, stated plainly.** A guard cannot tell "already created by
`0001`" from "already created by hand, with the wrong type". It matches on
*existence*, not on shape, so a column of the wrong type is skipped rather
than reported. That is why every skip logs a warning naming the object and
the revision: the chain stops crashing, but it does not go quiet.

The honest end state is `0001` creating the schema it created historically,
and this module deleting itself. Until then this is the difference between a
chain that can build a database and one that cannot.
"""

from __future__ import annotations

import functools
from typing import Any

import structlog
from sqlalchemy import inspect

logger = structlog.get_logger()

_INSTALLED = False


def _bind():
    from alembic import op

    return op.get_bind()


def _insp():
    return inspect(_bind())


def _has_table(name: str) -> bool:
    return _insp().has_table(name)


def _has_column(table: str, column: str) -> bool:
    try:
        return any(c["name"] == column for c in _insp().get_columns(table))
    except Exception:  # noqa: BLE001 — table absent means the column is too
        return False


def _has_index(table: str | None, name: str) -> bool:
    insp = _insp()
    tables = [table] if table else insp.get_table_names()
    for t in tables:
        try:
            if any(ix.get("name") == name for ix in insp.get_indexes(t)):
                return True
            # A UNIQUE CONSTRAINT is backed by an index of the same name and
            # collides the same way, so both have to count as "present".
            if any(uc.get("name") == name for uc in insp.get_unique_constraints(t)):
                return True
        except Exception:  # noqa: BLE001 — skip tables that do not exist yet
            continue
    return False


def _has_constraint(name: str) -> bool:
    """Any constraint with this name, in the public schema.

    Names are unique per schema in PostgreSQL, so a name lookup is enough and
    is far cheaper than reflecting every table's constraints by kind.
    """
    row = _bind().execute(
        __import__("sqlalchemy").text(
            "SELECT 1 FROM pg_constraint c"
            " JOIN pg_namespace n ON n.oid = c.connamespace"
            " WHERE n.nspname = 'public' AND c.conname = :n"
        ),
        {"n": name},
    )
    return row.first() is not None


def _skip(kind: str, name: str, *, creating: bool) -> None:
    logger.warning(
        "migration.object_skipped",
        kind=kind,
        name=name,
        action="create" if creating else "drop",
        detail=(
            "already exists; see migration_guards — 0001's create_all builds "
            "today's models, so later migrations meet their own objects"
            if creating
            else "not present; nothing to drop"
        ),
    )


# Each entry answers one question: given this op's arguments, what object is
# it about, and does that object already exist? Kept as data so adding an
# operation is one line and cannot accidentally change another's behaviour.
def _exists_create_table(a: tuple, k: dict) -> tuple[str, str] | None:
    return ("table", a[0]) if a else None


def _exists_add_column(a: tuple, k: dict) -> tuple[str, str] | None:
    if len(a) < 2:
        return None
    return ("column", f"{a[0]}.{getattr(a[1], 'name', a[1])}")


def _exists_create_index(a: tuple, k: dict) -> tuple[str, str] | None:
    return ("index", a[0]) if a else None


def _exists_named_constraint(a: tuple, k: dict) -> tuple[str, str] | None:
    return ("constraint", a[0]) if a else None


_CREATORS: dict[str, Any] = {
    "create_table": _exists_create_table,
    "add_column": _exists_add_column,
    "create_index": _exists_create_index,
    "create_foreign_key": _exists_named_constraint,
    "create_unique_constraint": _exists_named_constraint,
    "create_check_constraint": _exists_named_constraint,
    "create_primary_key": _exists_named_constraint,
}

_DROPPERS: dict[str, Any] = {
    "drop_table": _exists_create_table,
    "drop_column": _exists_add_column,
    "drop_index": _exists_create_index,
    "drop_constraint": _exists_named_constraint,
}


def _present(kind: str, name: str, args: tuple, kwargs: dict) -> bool:
    if kind == "table":
        return _has_table(name)
    if kind == "column":
        table, _, column = name.partition(".")
        return _has_column(table, column)
    if kind == "index":
        table = kwargs.get("table_name") or (args[1] if len(args) > 1 else None)
        return _has_index(table if isinstance(table, str) else None, name)
    return _has_constraint(name) or _has_index(None, name)


def _wrap(op_module, attr: str, describe, creating: bool):
    original = getattr(op_module, attr)

    @functools.wraps(original)
    def guarded(*args, **kwargs):
        try:
            described = describe(args, kwargs)
        except Exception:  # noqa: BLE001 — never let the guard break the op
            described = None
        if described is not None:
            kind, name = described
            try:
                exists = _present(kind, name, args, kwargs)
            except Exception:  # noqa: BLE001 — reflection failure: run the op
                exists = False
            if exists is creating:
                # creating and it exists, or dropping and it does not.
                _skip(kind, name, creating=creating)
                return None
        return original(*args, **kwargs)

    guarded.__wrapped_by_contextedge_guard__ = True  # type: ignore[attr-defined]
    return guarded


def install_idempotent_ddl_guards() -> bool:
    """Patch ``alembic.op`` so create/drop skip objects already in that state.

    Called from ``alembic/env.py`` before any revision runs. Idempotent: a
    second call is a no-op, so an ``env.py`` imported twice does not stack
    wrappers.
    """
    global _INSTALLED
    if _INSTALLED:
        return False
    from alembic import op

    for attr, describe in _CREATORS.items():
        if hasattr(op, attr):
            setattr(op, attr, _wrap(op, attr, describe, creating=True))
    for attr, describe in _DROPPERS.items():
        if hasattr(op, attr):
            setattr(op, attr, _wrap(op, attr, describe, creating=False))
    _INSTALLED = True
    logger.info("migration.ddl_guards_installed", operations=len(_CREATORS) + len(_DROPPERS))
    return True
