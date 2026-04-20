import asyncio
import uuid
from sqlalchemy import select
from contextedge.database import async_session_factory
from contextedge.models.episode import Episode
from contextedge.models.pattern import PatternEvidenceLink, Pattern

async def debug_clustering():
    async with async_session_factory() as db:
        print("--- Debugging Clustering Candidates ---")
        
        # Fetch all episodes
        res = await db.execute(select(Episode))
        episodes = res.scalars().all()
        print(f"Total Episodes found: {len(episodes)}")
        
        for ep in episodes:
            print(f"\nEpisode: '{ep.title}' (ID: {ep.id})")
            
            # Check Reviewer State
            print(f"  - Reviewer State: {ep.reviewer_state} (Expected: 'approved')")
            
            # Check Embedding
            print(f"  - Has Embedding: {ep.embedding is not None}")
            
            # Check Linking
            linked_res = await db.execute(
                select(PatternEvidenceLink)
                .join(Pattern, Pattern.id == PatternEvidenceLink.pattern_id)
                .where(PatternEvidenceLink.episode_id == ep.id)
            )
            link = linked_res.scalar_one_or_none()
            print(f"  - Already Linked to Pattern: {link is not None}")
            
            # Check for domain/tenant match (common source of hidden issues)
            print(f"  - Domain ID: {ep.domain_id}")
            print(f"  - Tenant ID: {ep.tenant_id}")
            
            is_candidate = (
                ep.reviewer_state == "approved" and 
                ep.embedding is not None and 
                link is None
            )
            print(f"  => IS CANDIDATE: {is_candidate}")

if __name__ == "__main__":
    asyncio.run(debug_clustering())
