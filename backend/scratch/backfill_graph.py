import asyncio
import sys
import os
import uuid

# Ensure we can import from src
sys.path.insert(0, os.path.join(os.getcwd(), "src"))

from sqlalchemy import select
from contextedge.database import create_db_engine, async_sessionmaker
from contextedge.models.pattern import Pattern, PatternEvidenceLink
from contextedge.models.episode import Episode
from contextedge.graph.builder import build_episode_graph
from contextedge.services.identity_service import identity_ids_from_refs

async def main():
    engine = create_db_engine(use_null_pool=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as db:
        # 1. Fetch all patterns
        p_res = await db.execute(select(Pattern))
        patterns = p_res.scalars().all()
        print(f"Checking {len(patterns)} patterns for missing graph edges...")

        for pattern in patterns:
            # 2. Get linked episodes
            l_res = await db.execute(
                select(PatternEvidenceLink.episode_id)
                .where(PatternEvidenceLink.pattern_id == pattern.id, PatternEvidenceLink.episode_id.is_not(None))
            )
            ep_ids = l_res.scalars().all()
            if not ep_ids:
                continue

            print(f"  Pattern: {pattern.title} ({len(ep_ids)} episodes)")
            
            # 3. Fetch episodes
            e_res = await db.execute(select(Episode).where(Episode.id.in_(ep_ids)))
            episodes = e_res.scalars().all()
            
            # 4. Create edges
            for ep in episodes:
                print(f"    - Linking Episode {ep.id}")
                await build_episode_graph(
                    db,
                    pattern.tenant_id,
                    ep.id,
                    pattern.id,
                    identity_ids_from_refs(ep.entity_refs),
                    domain_id=pattern.domain_id,
                )
        
        await db.commit()
        print("\nBackfill complete.")

    await engine.dispose()

if __name__ == "__main__":
    asyncio.run(main())
