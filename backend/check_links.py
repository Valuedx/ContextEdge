import asyncio
import sys
import os

# Ensure we can import from src
sys.path.insert(0, os.path.join(os.getcwd(), "src"))

from sqlalchemy import select, text
from contextedge.database import create_db_engine, async_sessionmaker
from contextedge.models.pattern import Pattern, PatternEvidenceLink
from contextedge.models.episode import Episode

async def main():
    engine = create_db_engine(use_null_pool=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as db:
        print("--- Checking Patterns ---")
        p_result = await db.execute(select(Pattern))
        patterns = p_result.scalars().all()
        print(f"Total Patterns: {len(patterns)}")
        for p in patterns:
            print(f"Pattern ID: {p.id}, Title: {p.title}, Tenant: {p.tenant_id}")

        print("\n--- Checking PatternEvidenceLinks ---")
        l_result = await db.execute(select(PatternEvidenceLink))
        links = l_result.scalars().all()
        print(f"Total Links: {len(links)}")
        for l in links:
            print(f"Link: Pattern={l.pattern_id}, Episode={l.episode_id}")

        print("\n--- Checking Approved Episodes ---")
        e_result = await db.execute(
            select(Episode).where(Episode.reviewer_state == "approved")
        )
        episodes = e_result.scalars().all()
        print(f"Total Approved Episodes: {len(episodes)}")
        for e in episodes:
            print(f"Episode ID: {e.id}, Title: {e.title}")

    await engine.dispose()

if __name__ == "__main__":
    asyncio.run(main())
