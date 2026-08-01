"""
Synchronously process all pending RawEvidenceObjects into EvidenceItems.

This script bypasses the Celery worker and runs the normalization & classification
logic directly in a loop. This is the most reliable way to process local data
on Windows when Celery workers encounter "unregistered task" or pathing errors.

Usage:
    cd backend
    $env:PYTHONPATH="src"; .\venv\\Scripts\\python.exe -m contextedge.scripts.process_all_raw
"""

import asyncio
import logging
import uuid

from sqlalchemy import select

from contextedge.database import async_session_factory
from contextedge.models.evidence import RawEvidenceObject
from contextedge.workers.extraction_tasks import _normalize

logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
log = logging.getLogger(__name__)

TENANT_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")

async def main():
    async with async_session_factory() as db:
        # 1. Get all raw objects for the tenant
        res = await db.execute(
            select(RawEvidenceObject).where(RawEvidenceObject.tenant_id == TENANT_ID)
        )
        raw_objects = res.scalars().all()

        log.info(f"Found {len(raw_objects)} raw evidence objects total.")

        processed_count = 0
        for obj in raw_objects:
            # 2. Check if already processed (to avoid duplicates)
            # Note: _normalize handles its own deduping, so we can just call it
            log.info(f"Processing: {obj.external_id} (ID: {obj.id})...")
            try:
                # IMPORTANT: pass TENANT_ID as UUID object, NOT string
                result = await _normalize(db, str(obj.id), TENANT_ID)
                if result.get("error"):
                    log.error(f"  - Error: {result.get('error')}")
                elif result.get("deduped"):
                    log.info("  - Already exists (deduped).")
                else:
                    log.info("  - Successfully processed.")
                    processed_count += 1
            except Exception as e:
                log.error(f"  - Failed to process {obj.external_id}: {e}")

        await db.commit()
        log.info(f"Finished! Processed {processed_count} new items.")

if __name__ == "__main__":
    asyncio.run(main())
