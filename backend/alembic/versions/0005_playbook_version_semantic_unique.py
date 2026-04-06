"""Enforce unique semantic versions per playbook."""

import re
from collections.abc import Sequence
from itertools import groupby

import sqlalchemy as sa

from alembic import op

revision: str = "0005_playbook_version_semantic_unique"
down_revision: str | None = "0004_evidence_access_policy_fk"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


SEMANTIC_VERSION_RE = re.compile(r"^(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)$")


def _parse_semantic_version(value: str) -> tuple[int, int, int] | None:
    match = SEMANTIC_VERSION_RE.fullmatch(value)
    if match is None:
        return None
    return (
        int(match.group("major")),
        int(match.group("minor")),
        int(match.group("patch")),
    )


def _next_free_semantic_version(used_versions: set[str]) -> str:
    parsed = [parsed for version in used_versions if (parsed := _parse_semantic_version(version))]
    if parsed:
        major, minor, patch = max(parsed)
        patch += 1
    else:
        major, minor, patch = 0, 1, 0

    candidate = f"{major}.{minor}.{patch}"
    while candidate in used_versions:
        patch += 1
        candidate = f"{major}.{minor}.{patch}"
    return candidate


def _canonical_duplicate_sort_key(row: dict) -> tuple:
    published_at = row["published_at"]
    created_at = row["created_at"]
    return (
        1 if row["is_current"] else 0,
        1 if published_at is not None else 0,
        published_at or created_at,
        created_at,
        str(row["id"]),
    )


def _ensure_alembic_version_column_capacity(bind: sa.Connection, minimum_length: int) -> None:
    current_length = bind.execute(
        sa.text(
            """
            SELECT character_maximum_length
            FROM information_schema.columns
            WHERE table_name = 'alembic_version'
              AND column_name = 'version_num'
            ORDER BY CASE WHEN table_schema = current_schema() THEN 0 ELSE 1 END
            LIMIT 1
            """
        )
    ).scalar_one_or_none()

    if current_length is None or current_length >= minimum_length:
        return

    op.execute(
        sa.text(
            "ALTER TABLE alembic_version ALTER COLUMN version_num TYPE VARCHAR(:length)"
        ).bindparams(length=minimum_length)
    )


def _has_unique_constraint(
    inspector: sa.Inspector,
    table_name: str,
    constraint_name: str,
    columns: list[str],
) -> bool:
    for constraint in inspector.get_unique_constraints(table_name):
        if constraint.get("name") != constraint_name:
            continue
        if constraint.get("column_names") != columns:
            continue
        return True

    for index in inspector.get_indexes(table_name):
        if index.get("name") != constraint_name:
            continue
        if not index.get("unique"):
            continue
        if index.get("column_names") != columns:
            continue
        return True

    return False


def upgrade() -> None:
    bind = op.get_bind()
    _ensure_alembic_version_column_capacity(bind, 255)
    existing_versions_rows = bind.execute(
        sa.text(
            """
            SELECT
                playbook_id,
                semantic_version
            FROM playbook_versions
            """
        )
    ).mappings()
    used_versions_by_playbook: dict[object, set[str]] = {}
    for row in existing_versions_rows:
        used_versions_by_playbook.setdefault(row["playbook_id"], set()).add(row["semantic_version"])

    duplicate_rows = bind.execute(
        sa.text(
            """
            SELECT
                pv.playbook_id,
                pv.semantic_version,
                pv.id,
                pv.published_at,
                pv.created_at,
                CASE WHEN p.current_version_id = pv.id THEN TRUE ELSE FALSE END AS is_current
            FROM playbook_versions AS pv
            JOIN playbooks AS p
              ON p.id = pv.playbook_id
            JOIN (
                SELECT playbook_id, semantic_version
                FROM playbook_versions
                GROUP BY playbook_id, semantic_version
                HAVING COUNT(*) > 1
            ) AS dup
              ON dup.playbook_id = pv.playbook_id
             AND dup.semantic_version = pv.semantic_version
            ORDER BY pv.playbook_id, pv.semantic_version
            """
        )
    ).mappings()

    for (playbook_id, _semantic_version), grouped in groupby(
        duplicate_rows,
        key=lambda row: (row["playbook_id"], row["semantic_version"]),
    ):
        rows = list(grouped)
        keep_row = max(rows, key=_canonical_duplicate_sort_key)
        used_versions = used_versions_by_playbook.setdefault(playbook_id, set())
        for row in rows:
            if row["id"] == keep_row["id"]:
                continue

            candidate = _next_free_semantic_version(used_versions)
            bind.execute(
                sa.text(
                    """
                    UPDATE playbook_versions
                    SET semantic_version = :semantic_version
                    WHERE id = :version_id
                    """
                ),
                {
                    "semantic_version": candidate,
                    "version_id": row["id"],
                },
            )
            used_versions.add(candidate)

    inspector = sa.inspect(bind)
    if not _has_unique_constraint(
        inspector,
        "playbook_versions",
        "uq_playbook_versions_playbook_semantic_version",
        ["playbook_id", "semantic_version"],
    ):
        op.create_unique_constraint(
            "uq_playbook_versions_playbook_semantic_version",
            "playbook_versions",
            ["playbook_id", "semantic_version"],
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if _has_unique_constraint(
        inspector,
        "playbook_versions",
        "uq_playbook_versions_playbook_semantic_version",
        ["playbook_id", "semantic_version"],
    ):
        op.drop_constraint(
            "uq_playbook_versions_playbook_semantic_version",
            "playbook_versions",
            type_="unique",
        )
