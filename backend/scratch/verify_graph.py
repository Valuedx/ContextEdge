import asyncio
import sys
import os
import uuid

# Ensure we can import from src
sys.path.insert(0, os.path.join(os.getcwd(), "src"))

from sqlalchemy import select
from contextedge.database import create_db_engine, async_sessionmaker
from contextedge.models.pattern import GraphEdge, Pattern, PatternEvidenceLink
from contextedge.models.episode import Episode

async def main():
    engine = create_db_engine(use_null_pool=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as db:
        # 1. Get the most recent pattern
        p_res = await db.execute(select(Pattern).order_by(Pattern.created_at.desc()).limit(1))
        pattern = p_res.scalar_one_or_none()
        if not pattern:
            print("No patterns found. Create one first.")
            return
        
        print(f"Checking Graph for Pattern: {pattern.title} ({pattern.id})")
        
        # 2. Check belongs_to edges
        b_res = await db.execute(
            select(GraphEdge).where(
                GraphEdge.target_node_id == pattern.id,
                GraphEdge.edge_type == "belongs_to"
            )
        )
        belongs_to_edges = b_res.scalars().all()
        print(f"Found {len(belongs_to_edges)} 'belongs_to' edges from episodes.")
        for edge in belongs_to_edges:
            print(f"  - Episode {edge.source_node_id} -> Pattern")

        # 3. Check enrichment edges
        e_res = await db.execute(
            select(GraphEdge).where(
                GraphEdge.target_node_id == pattern.id,
                GraphEdge.edge_type.in_(["trigger_of", "involved_in", "discovered_in", "causes"])
            )
        )
        enrichment_edges = e_res.scalars().all()
        print(f"Found {len(enrichment_edges)} enrichment edges (virtual nodes).")
        for edge in enrichment_edges:
            print(f"  - {edge.source_node_type} [{edge.metadata_extra.get('label')}] -> {edge.edge_type} -> Pattern")

        # 4. Check affects edges from episodes
        if belongs_to_edges:
            ep_ids = [edge.source_node_id for edge in belongs_to_edges]
            a_res = await db.execute(
                select(GraphEdge).where(
                    GraphEdge.source_node_id.in_(ep_ids),
                    GraphEdge.edge_type == "affects"
                )
            )
            affects_edges = a_res.scalars().all()
            print(f"Found {len(affects_edges)} 'affects' edges from episodes to identities.")

    await engine.dispose()

if __name__ == "__main__":
    asyncio.run(main())
