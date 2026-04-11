import asyncio
import uuid
from contextedge.config import settings
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select
from contextedge.models.evidence import EvidenceItem

async def check():
    engine = create_async_engine(settings.database_url)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    
    async with async_session() as db:
        res = await db.execute(
            select(EvidenceItem).order_by(EvidenceItem.ingested_at.desc()).limit(10)
        )
        items = res.scalars().all()
        print("LATEST EVIDENCE ITEMS:")
        for item in items:
            print(f"  ID: {item.id}")
            print(f"  Title: {item.title or 'Untitled'}")
            print(f"  State: {item.relevance_state}")
            print(f"  Score: {item.relevance_score}")
            print(f"  Type: {item.evidence_type}")
            print("-" * 20)

if __name__ == "__main__":
    asyncio.run(check())
