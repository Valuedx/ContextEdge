import asyncio
from contextedge.config import settings
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select
from contextedge.models.evidence import EvidenceItem
from datetime import datetime, UTC

async def check():
    engine = create_async_engine(settings.database_url)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    
    async with async_session() as db:
        # Check all latest evidence
        res = await db.execute(
            select(EvidenceItem).order_by(EvidenceItem.ingested_at.desc()).limit(20)
        )
        items = res.scalars().all()
        print(f"REPORT AT {datetime.now(UTC)}")
        print(f"TOTAL ITEMS RETURNED: {len(items)}")
        for i in items:
            print(f"  [{i.id}]")
            print(f"    Tenant: {i.tenant_id}")
            print(f"    State:  {i.relevance_state}")
            print(f"    Ingest: {i.ingested_at}")
            print(f"    Type:   {i.evidence_type}")
            print("-" * 10)

if __name__ == "__main__":
    asyncio.run(check())
