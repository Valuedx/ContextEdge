"""Regression coverage for pgvector's 2000-dimension HNSW vector limit."""

from __future__ import annotations

import importlib.util
from contextlib import nullcontext
from pathlib import Path

import pytest


MIGRATIONS_DIR = Path(__file__).parents[1] / "alembic" / "versions"


class _FakeContext:
    def autocommit_block(self):
        return nullcontext()


class _FakeOperations:
    def __init__(self):
        self.statements: list[str] = []

    def get_context(self):
        return _FakeContext()

    def execute(self, statement: str):
        self.statements.append(statement)


def _load_migration(revision: str):
    path = next(MIGRATIONS_DIR.glob(f"{revision}_*.py"))
    spec = importlib.util.spec_from_file_location(f"migration_{revision}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("revision", "index_names"),
    [
        (
            "0021",
            (
                "ix_evidence_items_embedding_hnsw",
                "ix_decisions_embedding_hnsw",
            ),
        ),
        ("0030", ("ix_evidence_chunks_embedding_hnsw",)),
    ],
)
def test_wide_vector_hnsw_migrations_skip_unsupported_indexes(
    revision: str,
    index_names: tuple[str, ...],
):
    migration = _load_migration(revision)
    operations = _FakeOperations()
    migration.op = operations

    migration.upgrade()

    statements = "\n".join(operations.statements)
    for index_name in index_names:
        assert f"DROP INDEX CONCURRENTLY IF EXISTS {index_name}" in statements
    assert "USING hnsw" not in statements
