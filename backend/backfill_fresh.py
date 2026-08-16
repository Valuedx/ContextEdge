import sys, asyncio, time
sys.path.insert(0, "src")
from sqlalchemy import select
from contextedge.services.sync_worker_service import run_backfill_job
from contextedge.database import async_session_factory
from contextedge.models.source import Source, SourceObject

async def main():
    async with async_session_factory() as db:
        src = (await db.execute(select(Source).where(Source.source_type == "zoho_desk"))).scalars().first()
        objs = (await db.execute(select(SourceObject).where(SourceObject.source_id == src.id))).scalars().all()
        tix = next(o for o in objs if o.external_id == "tickets")
        t0 = time.time()
        print(f"[backfill] start tickets object={tix.id}", flush=True)
        r = await run_backfill_job(db, src.id, tix.id, src.tenant_id, window_days=3650)
        await db.commit()
        print(f"[backfill] DONE {r} in {time.time()-t0:.0f}s", flush=True)

asyncio.run(main())
