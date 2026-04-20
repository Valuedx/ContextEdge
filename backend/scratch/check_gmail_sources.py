import asyncio
import sys
import os

# Ensure we can import from src
sys.path.insert(0, os.path.join(os.getcwd(), "src"))

from sqlalchemy import select
from contextedge.database import create_db_engine, async_sessionmaker
from contextedge.models.source import Source

async def main():
    engine = create_db_engine(use_null_pool=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as db:
        res = await db.execute(select(Source).where(Source.source_type == "gmail"))
        sources = res.scalars().all()
        print(f"Total Gmail Sources: {len(sources)}")
        for s in sources:
            print(f"- {s.display_name} (ID: {s.id}) status={s.auth_status}")

    await engine.dispose()

if __name__ == "__main__":
    asyncio.run(main())
