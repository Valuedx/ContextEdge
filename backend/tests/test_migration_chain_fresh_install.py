"""The migration chain must build a database from nothing.

It could not, for a long time. `0001` creates the schema with
`Base.metadata.create_all`, which reflects whatever the models declare *today*
rather than what they declared at `0001`, so every later revision that creates
something still present in the models met its own object. Measured before the
fix: **17 of 96 revisions failed**, the first at `0070`.

The cost was not the error. It was that nothing could be tested against a real
database, which is why this suite had 2 integration tests and why schema
governance is enforced by scanning migration *text* rather than running it.

This test exists so that cannot regress quietly. It is slow — a container and
a full chain — and it is worth it: it is the only test that proves a new
deployment is possible at all.

The assertions go past "exit 0" on purpose. A guarded migration that skips too
much would still exit 0 and leave a database with no row-level security, which
is a far worse outcome than a crash. So the security DDL is counted, not
assumed.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
import sqlalchemy as sa

BACKEND = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def freshly_migrated():
    """A database built ONLY by `alembic upgrade head`, from empty."""
    testcontainers = pytest.importorskip("testcontainers")
    if not hasattr(testcontainers, "postgres"):
        pytest.importorskip("testcontainers.postgres")
    from testcontainers.postgres import PostgresContainer

    try:
        container = PostgresContainer("pgvector/pgvector:pg16")
        container.start()
    except Exception as exc:  # noqa: BLE001 — docker genuinely unavailable
        pytest.skip(f"PostgreSQL container unavailable: {exc}")

    try:
        url = container.get_connection_url()
        env = os.environ.copy()
        env["DATABASE_URL_SYNC"] = url
        env["DATABASE_URL"] = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        proc = subprocess.run(  # noqa: S603
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=BACKEND,
            env=env,
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, (
            "the migration chain cannot build a fresh database:\n"
            + proc.stderr[-4000:]
        )
        yield url
    finally:
        container.stop()


def _scalar(url: str, sql: str) -> int:
    engine = sa.create_engine(url)
    try:
        with engine.connect() as conn:
            return int(conn.execute(sa.text(sql)).scalar_one())
    finally:
        engine.dispose()


@pytest.mark.integration
def test_the_chain_reaches_head_from_an_empty_database(freshly_migrated):
    """The fixture asserts the upgrade succeeded; this pins the end state."""
    stamped = _scalar(freshly_migrated, "SELECT count(*) FROM alembic_version")
    assert stamped == 1, "exactly one head revision should be stamped"


@pytest.mark.integration
def test_a_fresh_database_has_the_isolation_a_migrated_one_has(freshly_migrated):
    """Guards must not have skipped the security DDL.

    This is the failure mode that matters. A guard matches on existence, so
    one that fires too eagerly would leave the chain green and the database
    unprotected — every table present, no policy on any of them. Counting is
    the only way to tell those apart from outside.
    """
    scoped = _scalar(
        freshly_migrated,
        "SELECT count(*) FROM information_schema.columns"
        " WHERE table_schema='public' AND column_name='tenant_id'",
    )
    assert scoped > 50, f"suspiciously few tenant-scoped tables: {scoped}"

    # Every tenant-scoped table, PLUS the two that define the hierarchy.
    # `msps` and `tenants` are not reached by the policy loop — one has no
    # tenant_id, the other is excluded by name — and are policed explicitly,
    # so the expected count is scoped + 2 and both must be present by name.
    for policy in ("ce_msp_isolation", "ce_client_isolation"):
        count = _scalar(
            freshly_migrated,
            f"SELECT count(*) FROM pg_policies WHERE policyname='{policy}'",
        )
        assert count == scoped + 2, (
            f"{policy} is on {count} tables; expected {scoped} scoped tables "
            "plus msps and tenants"
        )
        for table in ("msps", "tenants"):
            present = _scalar(
                freshly_migrated,
                "SELECT count(*) FROM pg_policies"
                f" WHERE policyname='{policy}' AND tablename='{table}'",
            )
            assert present == 1, (
                f"{table} has no {policy}; a client could read every row of it"
            )

    forced = _scalar(
        freshly_migrated,
        "SELECT count(*) FROM pg_class WHERE relrowsecurity AND relforcerowsecurity",
    )
    assert forced == scoped + 2, (
        f"FORCE RLS on {forced} tables; expected {scoped} scoped plus msps and tenants"
    )

    triggers = _scalar(
        freshly_migrated,
        "SELECT count(*) FROM pg_trigger WHERE tgname='trg_ce_fill_msp_id'",
    )
    assert triggers == scoped, (
        f"the msp_id fill trigger is on {triggers} of {scoped} tables; rows "
        "inserted into the rest would carry a NULL msp_id and be invisible"
    )


@pytest.mark.integration
def test_the_superseded_bypassable_policy_is_gone(freshly_migrated):
    """`0078`'s `tenant_isolation` read an application-controlled GUC.

    `0097` replaces it. On a fresh build the replacement has to be complete:
    a table left with the old policy is one where setting a session variable
    still turns isolation off.
    """
    legacy = _scalar(
        freshly_migrated,
        "SELECT count(*) FROM pg_policies WHERE policyname='tenant_isolation'",
    )
    assert legacy == 0, f"{legacy} tables still carry the bypassable policy"


@pytest.mark.integration
def test_the_application_roles_exist_and_cannot_bypass(freshly_migrated):
    """A fresh install must arrive with the roles, and without BYPASSRLS."""
    roles = _scalar(
        freshly_migrated,
        "SELECT count(*) FROM pg_roles WHERE rolname LIKE 'ce_app_%'",
    )
    assert roles == 3, f"expected platform/msp/client roles, found {roles}"

    safe = _scalar(
        freshly_migrated,
        "SELECT count(*) FROM pg_roles WHERE rolname LIKE 'ce_app_%' AND NOT rolbypassrls",
    )
    assert safe == roles, "an application role holds BYPASSRLS"
