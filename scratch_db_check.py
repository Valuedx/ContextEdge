import asyncio
import os
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text
from dotenv import load_dotenv

load_dotenv()

async def check_stats():
    url = os.getenv("DATABASE_URL")
    if not url:
        print("DATABASE_URL not found")
        return
    
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        tables = ["sources", "evidence_items", "episodes", "playbooks"]
        for table in tables:
            try:
                res = await conn.execute(text(f"SELECT COUNT(*) FROM {table}"))
                count = res.scalar()
                print(f"{table}: {count}")
            except Exception as e:
                print(f"Error checking {table}: {e}")
    await engine.dispose()

if __name__ == "__main__":
    asyncio.run(check_stats())
