import asyncio
from sqlalchemy import select, func
from contextedge.database import async_session_factory
from contextedge.models.evidence import EvidenceItem, RawEvidenceObject

async def check():
    async with async_session_factory() as db:
        raw_count = (await db.execute(select(func.count()).select_from(RawEvidenceObject))).scalar()
        ev_count = (await db.execute(select(func.count()).select_from(EvidenceItem))).scalar()
        
        print(f"RawEvidenceObject count: {raw_count}")
        print(f"EvidenceItem count:      {ev_count}")
        
        if ev_count > 0:
            print("\nLatest items:")
            items = (await db.execute(select(EvidenceItem).order_by(EvidenceItem.ingested_at.desc()).limit(5))).scalars().all()
            for item in items:
                print(f"- [{item.evidence_type}] {item.title} (Relevance: {item.relevance_state})")

if __name__ == "__main__":
    asyncio.run(check())
