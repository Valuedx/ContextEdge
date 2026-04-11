import asyncio
import uuid
from contextedge.deps import get_db
from contextedge.graph.queries import get_pattern_subgraph
from contextedge.models.pattern import Pattern
from sqlalchemy import select

async def verify():
    async for db in get_db():
        # Get the latest pattern
        res = await db.execute(select(Pattern).order_by(Pattern.created_at.desc()).limit(1))
        pattern = res.scalar_one_or_none()
        
        if not pattern:
            print("No patterns found. Discovery needs to be triggered first.")
            return

        print(f"Verifying Pattern: {pattern.id} - {pattern.title}")
        print(f"Trigger Conditions: {pattern.trigger_conditions}")
        print(f"Entities: {pattern.core_entities}")
        
        subgraph = await get_pattern_subgraph(db, pattern.tenant_id, pattern.id)
        
        print("\nSubgraph Nodes:")
        for node in subgraph['nodes']:
            print(f"- {node['type']}: {node.get('title', node['id'])}")
            
        print("\nSubgraph Edges:")
        for edge in subgraph['edges']:
            print(f"- {edge['source']} --({edge['type']})--> {edge['target']}")
            
        break

if __name__ == "__main__":
    asyncio.run(verify())
