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
        res = await db.execute(select(Source).order_by(Source.created_at.desc()).limit(1))
        s = res.scalar_one_or_none()
        if s:
            print(f"Latest Source: {s.display_name} ({s.source_type}) - Created at {s.created_at}")
        else:
            print("No sources found.")

    await engine.dispose()

if __name__ == "__main__":
    asyncio.run(main())
