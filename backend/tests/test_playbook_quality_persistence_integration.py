"""Integration test for playbook-quality persistence against real PostgreSQL.

Runs inside a rolled-back transaction when a database is available. With
``testcontainers`` installed and Docker running, spins up Postgres 16, applies
migrations, and runs the same checks as ``scripts/verify_quality_persistence.py``.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _alembic_upgrade(sync_url: str) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = sync_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    env["DATABASE_URL_SYNC"] = sync_url
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND_ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.fixture(scope="module")
def postgres_dsn():
    testcontainers = pytest.importorskip("testcontainers")
    if not hasattr(testcontainers, "postgres"):
        pytest.importorskip("testcontainers.postgres")
    from testcontainers.postgres import PostgresContainer

    try:
        with PostgresContainer("postgres:16-alpine") as postgres:
            sync_url = postgres.get_connection_url()
            _alembic_upgrade(sync_url)
            async_url = sync_url.replace("postgresql://", "postgresql+asyncpg://", 1)
            yield async_url
    except Exception as exc:  # noqa: BLE001 — docker unavailable
        pytest.skip(f"PostgreSQL container unavailable: {exc}")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_quality_persistence_checks_on_postgres(postgres_dsn: str):
    from contextedge.models.tenant import Tenant
    from contextedge.quality.persistence_verification import verify_quality_persistence

    engine = create_async_engine(postgres_dsn)
    async with AsyncSession(engine) as db:
        tenant = Tenant(
            id=uuid.uuid4(),
            name=f"quality-verify-{uuid.uuid4().hex[:8]}",
            slug=f"qv-{uuid.uuid4().hex[:8]}",
        )
        db.add(tenant)
        await db.flush()

        outcome = await verify_quality_persistence(db, tenant.id)
        await db.rollback()

    await engine.dispose()
    assert outcome.ok, outcome.failed


@pytest.mark.integration
@pytest.mark.asyncio
async def test_quality_persistence_against_configured_database():
    """Runs when ``DATABASE_URL`` points at a migrated database (CI optional job)."""
    if os.environ.get("RUN_QUALITY_PERSISTENCE") != "1":
        pytest.skip("set RUN_QUALITY_PERSISTENCE=1 to run against configured DATABASE_URL")

    from contextedge.config import settings
    from contextedge.models.tenant import Tenant
    from contextedge.quality.persistence_verification import verify_quality_persistence

    dsn = settings.database_url
    engine = create_async_engine(dsn)
    async with AsyncSession(engine) as db:
        tenant_id = (await db.execute(sa.select(Tenant.id).limit(1))).scalar_one_or_none()
        if tenant_id is None:
            pytest.skip("no tenant in configured database")
        outcome = await verify_quality_persistence(db, tenant_id)
        await db.rollback()

    await engine.dispose()
    assert outcome.ok, outcome.failed
