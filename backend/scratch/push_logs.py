import asyncio
import uuid
from contextedge.config import settings
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select
from datetime import datetime, date
from contextedge.models.evidence import RawEvidenceObject, EvidenceItem
from contextedge.workers.extraction_tasks import _normalize, _classify

async def process_stuck():
    engine = create_async_engine(settings.database_url)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    
    async with async_session() as db:
        # Find raw objects from today that don't have evidence items
        today = date(2026, 4, 10)
        q = select(RawEvidenceObject).where(
            RawEvidenceObject.created_at >= today
        )
        raws = (await db.execute(q)).scalars().all()
        print(f"Found {len(raws)} raw objects to process.")
        
        for raw in raws:
            # Check if evidence item already exists for this raw ref
            check_q = select(EvidenceItem).where(EvidenceItem.raw_object_ref == raw.id)
            existing = (await db.execute(check_q)).scalar_one_or_none()
            
            if not existing:
                print(f"Processing {raw.id} ({raw.raw_payload.get('filename')})...")
                res = await _normalize(db, str(raw.id), raw.tenant_id)
                if "evidence_id" in res:
                    await _classify(db, res["evidence_id"], raw.tenant_id)
                print(f"  Done: {res.get('evidence_id')}")
            else:
                print(f"Skipping {raw.id} (Already exists)")
        
        await db.commit()
    print("\nFinished processing stuck logs.")

if __name__ == "__main__":
    asyncio.run(process_stuck())
