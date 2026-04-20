import asyncio
import sys
import os

# Ensure we can import from src
sys.path.insert(0, os.path.join(os.getcwd(), "src"))

from sqlalchemy import select
from contextedge.database import create_db_engine, async_sessionmaker
from contextedge.models.source import Source

async def check():
    engine = create_db_engine()
    factory = async_sessionmaker(engine)
    async with factory() as db:
        res = await db.execute(select(Source).where(Source.id == '31dff93d-774c-402c-af07-00d932c5b108'))
        s = res.scalar_one_or_none()
        if s:
            print(f"DISPLAYNAME: {s.display_name}")
            print(f"CONFIG: {s.config}")
        else:
            print("NOT FOUND")
    await engine.dispose()

if __name__ == "__main__":
    asyncio.run(check())
