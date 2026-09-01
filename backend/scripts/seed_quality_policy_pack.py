"""Seed default quality policy pack and ontology for a tenant.

    python backend/scripts/seed_quality_policy_pack.py
    python backend/scripts/seed_quality_policy_pack.py --tenant <uuid>
    python backend/scripts/seed_quality_policy_pack.py --dsn postgresql+asyncpg://...

With no ``--tenant`` it seeds every tenant in the database. Idempotent: skips
tenants that already have an active pack or ontology version.
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

from contextedge.config import settings  # noqa: E402
from contextedge.models.tenant import Tenant  # noqa: E402
from contextedge.services.quality_policy_service import (  # noqa: E402
    seed_default_ontology,
    seed_default_policy_pack,
)


async def seed_tenant(db: AsyncSession, tenant_id: uuid.UUID) -> None:
    pack = await seed_default_policy_pack(db, tenant_id)
    ont = await seed_default_ontology(db, tenant_id)
    print(f"  tenant {tenant_id}: pack v{pack.version}, ontology v{ont.version}")


async def run(dsn: str, tenant_id: uuid.UUID | None) -> int:
    engine = create_async_engine(dsn)
    async with AsyncSession(engine) as db:
        if tenant_id is not None:
            await seed_tenant(db, tenant_id)
        else:
            rows = (await db.execute(sa.select(Tenant.id))).scalars().all()
            if not rows:
                print("No tenants found; pass --tenant or create one first.")
                return 1
            for tid in rows:
                await seed_tenant(db, tid)
        await db.commit()

    await engine.dispose()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", default=settings.database_url, help="Async SQLAlchemy DSN")
    parser.add_argument("--tenant", type=uuid.UUID, default=None, help="Single tenant UUID")
    args = parser.parse_args()
    return asyncio.run(run(args.dsn, args.tenant))


if __name__ == "__main__":
    raise SystemExit(main())
