"""End-to-end verification of the playbook-quality persistence layer.

The unit suite in ``tests/test_playbook_quality_foundation.py`` covers the pure
logic — hashing, state resolution, validators — with no database. This script
covers the half that suite cannot reach: that the SQL in
``services/playbook_quality_service.py`` is valid against a real PostgreSQL,
that the constraints in migration 0094 do what they claim, and that append-only
supersede and staleness behave on actual rows.

    python backend/scripts/verify_quality_persistence.py
    python backend/scripts/verify_quality_persistence.py --dsn postgresql+asyncpg://...
    python backend/scripts/verify_quality_persistence.py --tenant <uuid>

With no ``--dsn`` it reads ``settings.database_url``. With no ``--tenant`` it
picks the first tenant it finds; the rows it creates are discarded either way.

Exit code is 0 when every check passes, 1 otherwise, so it can be wired into a
deployment check after ``alembic upgrade head``.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import sqlalchemy as sa  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine  # noqa: E402

from contextedge.models.tenant import Tenant  # noqa: E402
from contextedge.quality.persistence_verification import verify_quality_persistence  # noqa: E402


async def run(dsn: str, tenant_id: uuid.UUID | None) -> int:
    engine = create_async_engine(dsn)
    async with AsyncSession(engine) as db:
        if tenant_id is None:
            tenant_id = (
                await db.execute(sa.select(Tenant.id).limit(1))
            ).scalar_one_or_none()
            if tenant_id is None:
                print("No tenant found; pass --tenant or seed one first.")
                return 1
        print(f"tenant {tenant_id}\n")

        outcome = await verify_quality_persistence(db, tenant_id)
        for label in outcome.passed:
            print(f"  ok    {label}")
        for label in outcome.failed:
            print(f"  FAIL  {label}")

        await db.rollback()

    await engine.dispose()

    print(f"\n{len(outcome.passed)} passed, {len(outcome.failed)} failed")
    return 0 if outcome.ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", help="async SQLAlchemy DSN; defaults to settings.database_url")
    parser.add_argument("--tenant", help="tenant UUID; defaults to the first one found")
    args = parser.parse_args()

    dsn = args.dsn
    if not dsn:
        from contextedge.config import settings

        dsn = settings.database_url
    if "+asyncpg" not in dsn:
        dsn = dsn.replace("postgresql://", "postgresql+asyncpg://", 1)

    tenant = uuid.UUID(args.tenant) if args.tenant else None
    return asyncio.run(run(dsn, tenant))


if __name__ == "__main__":
    raise SystemExit(main())
