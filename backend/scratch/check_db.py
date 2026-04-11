import asyncio
from contextedge.config import settings
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

async def check():
    engine = create_async_engine(settings.database_url)
    async with engine.connect() as conn:
        raw = (await conn.execute(text('SELECT count(*) FROM raw_evidence_objects'))).scalar()
        ev = (await conn.execute(text('SELECT count(*) FROM evidence_items'))).scalar()
        print(f"RAW_OBJECTS: {raw}")
        print(f"EVIDENCE_ITEMS: {ev}")
        
        # Check recent ones
        recent_raw = await conn.execute(text('SELECT id, created_at FROM raw_evidence_objects ORDER BY created_at DESC LIMIT 5'))
        print("\nRECENT RAW OBJECTS:")
        for r in recent_raw:
            print(f"  {r.id} at {r.created_at}")

        recent_ev = await conn.execute(text('SELECT id, title, ingested_at FROM evidence_items ORDER BY ingested_at DESC LIMIT 5'))
        print("\nRECENT EVIDENCE ITEMS:")
        for e in recent_ev:
            print(f"  {e.id}: {e.title} at {e.ingested_at}")

if __name__ == "__main__":
    asyncio.run(check())
