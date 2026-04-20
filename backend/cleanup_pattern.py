import asyncio
import sys
import os

# Ensure we can import from src
sys.path.insert(0, os.path.join(os.getcwd(), "src"))

from sqlalchemy import delete
from contextedge.database import create_db_engine, async_sessionmaker
from contextedge.models.pattern import Pattern, PatternEvidenceLink

async def main():
    engine = create_db_engine(use_null_pool=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as db:
        pid = 'be39ca89-89e6-4d6d-8031-8e427c4ef315'
        print(f"Deleting pattern {pid} and its links...")
        
        # 1. Delete links
        res_l = await db.execute(delete(PatternEvidenceLink).where(PatternEvidenceLink.pattern_id == pid))
        print(f"Deleted {res_l.rowcount} links.")
        
        # 2. Delete pattern
        res_p = await db.execute(delete(Pattern).where(Pattern.id == pid))
        print(f"Deleted {res_p.rowcount} pattern.")
        
        await db.commit()

    await engine.dispose()

if __name__ == "__main__":
    asyncio.run(main())
