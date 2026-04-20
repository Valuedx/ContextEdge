import asyncio
import uuid
from sqlalchemy import select
from contextedge.database import engine, async_session_factory
from contextedge.models.source import SyncRun

async def get_run_error(run_id_str: str):
    run_id = uuid.UUID(run_id_str)
    async with async_session_factory() as db:
        result = await db.execute(select(SyncRun).where(SyncRun.id == run_id))
        run = result.scalar_one_or_none()
        if not run:
            print(f"Run {run_id_str} not found")
            return
        
        print(f"Run ID: {run.id}")
        print(f"Status: {run.status}")
        print(f"Run Type: {run.run_type}")
        print(f"Errors: {run.errors}")

if __name__ == "__main__":
    import sys
    rid = sys.argv[1] if len(sys.argv) > 1 else "951598da-8b6c-4dd2-89bc-152743b0be29"
    asyncio.run(get_run_error(rid))
