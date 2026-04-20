"""Diagnostic: print all episodes and their reviewer_state / tenant_id / embedding status."""
import asyncio
import sys
sys.path.insert(0, "src")

from sqlalchemy import select, text
from contextedge.database import create_db_engine, async_sessionmaker
from contextedge.models.episode import Episode


async def main():
    engine = create_db_engine(use_null_pool=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as db:
        # 1. Raw count
        r = await db.execute(text("SELECT COUNT(*) FROM episodes"))
        print(f"Total episodes in DB: {r.scalar()}")

        # 2. All episodes - key fields
        r2 = await db.execute(
            select(
                Episode.id,
                Episode.title,
                Episode.tenant_id,
                Episode.domain_id,
                Episode.reviewer_state,
                Episode.status,
            ).order_by(Episode.created_at.desc()).limit(20)
        )
        rows = r2.all()
        print(f"\nFound {len(rows)} episode rows:\n")
        for row in rows:
            print(f"  id={row.id}")
            print(f"  title={row.title[:60]!r}")
            print(f"  tenant_id={row.tenant_id}")
            print(f"  domain_id={row.domain_id}")
            print(f"  reviewer_state={row.reviewer_state!r}")
            print(f"  status={row.status!r}")
            print()

        # 3. Embedding check
        r3 = await db.execute(
            text("SELECT id, title, embedding IS NOT NULL as has_embedding FROM episodes")
        )
        print("Embedding status:")
        for row in r3.all():
            print(f"  {row.title[:50]!r}  has_embedding={row.has_embedding}")

        # 4. Tenants
        r4 = await db.execute(text("SELECT id, name FROM tenants"))
        print("\nTenants:")
        for row in r4.all():
            print(f"  id={row.id}  name={row.name!r}")

        # 5. Domains
        r5 = await db.execute(text("SELECT id, name, tenant_id FROM domains"))
        print("\nDomains:")
        for row in r5.all():
            print(f"  id={row.id}  name={row.name!r}  tenant_id={row.tenant_id}")

    await engine.dispose()


asyncio.run(main())
