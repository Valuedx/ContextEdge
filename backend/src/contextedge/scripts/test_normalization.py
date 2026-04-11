import asyncio
import uuid
import logging
from contextedge.database import async_session_factory
from contextedge.workers.extraction_tasks import _normalize, _classify

logging.basicConfig(level=logging.INFO)

async def test():
    tenant_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    async with async_session_factory() as db:
        from sqlalchemy import select
        from contextedge.models.evidence import RawEvidenceObject
        
        result = await db.execute(select(RawEvidenceObject).limit(1))
        raw = result.scalar_one_or_none()
        
        if not raw:
            print("No raw evidence found.")
            return
        
        print(f"Testing normalization for {raw.id} ({raw.external_id})...")
        try:
            res = await _normalize(db, str(raw.id), tenant_id)
            print(f"Normalization Result: {res}")
            
            if "evidence_id" in res:
                print(f"Testing classification for {res['evidence_id']}...")
                c_res = await _classify(db, res['evidence_id'], tenant_id)
                print(f"Classification Result: {c_res}")
                
            await db.commit()
            print("Successfully committed changes.")
        except Exception as e:
            print(f"FAILED: {e}")
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test())
