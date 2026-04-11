"""
Create an Episode from all normalized evidence in the database.

Usage:
    cd backend
    $env:PYTHONPATH="src"; .\\venv\\Scripts\\python.exe -m contextedge.scripts.create_episode
"""

import asyncio
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from contextedge.database import async_session_factory
from contextedge.models.evidence import EvidenceItem
from contextedge.services.episode_service import create_episode_from_evidence

logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
log = logging.getLogger(__name__)

# Default tenant used by the local ingestion script
TENANT_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


async def main() -> None:
    async with async_session_factory() as db:
        # 1. Load all normalized evidence items for the tenant
        result = await db.execute(
            select(EvidenceItem)
            .where(EvidenceItem.tenant_id == TENANT_ID)
            .order_by(EvidenceItem.ingested_at.asc())
        )
        items: list[EvidenceItem] = list(result.scalars().all())

        if not items:
            log.error("No EvidenceItems found. Run the ingestion script first.")
            return

        log.info(f"Found {len(items)} evidence items. Building episode thread...")

        # 2. Format evidence items into the structure the extractor expects
        evidence_dicts = [
            {
                "title": item.title or "Untitled",
                "body": item.body_text or "",
                "source_type": "local_file",
                "timestamp": str(item.ingested_at),
                "evidence_id": str(item.id),
            }
            for item in items
        ]
        evidence_ids = [item.id for item in items]

        # 3. Call the episode service (runs Gemini extraction internally)
        log.info("Calling Gemini to reconstruct the episode. This may take ~30 seconds...")
        try:
            episode = await create_episode_from_evidence(
                db,
                tenant_id=TENANT_ID,
                domain_id=None,
                evidence_items=evidence_dicts,
                evidence_ids=evidence_ids,
            )
            await db.commit()

            # Eagerly reload the episode with its steps to avoid lazy-load errors
            from contextedge.models.episode import Episode
            episode = (
                await db.execute(
                    select(Episode)
                    .where(Episode.id == episode.id)
                    .options(selectinload(Episode.steps))
                )
            ).scalar_one()

            log.info("Episode created successfully!")
            log.info(f"   ID     : {episode.id}")
            log.info(f"   Title  : {episode.title}")
            log.info(f"   Steps  : {len(episode.steps)}")
            log.info(f"   Root cause: {episode.root_cause_summary}")
            log.info(f"   Outcome   : {episode.final_outcome}")
            log.info(f"   Confidence: {episode.extraction_confidence:.0%}")
            log.info("")
            log.info("Step breakdown:")
            for step in episode.steps:
                flag = "FAILED" if step.failed_flag else ("OK" if step.successful_flag else "-")
                log.info(f"  [{flag}] [{step.step_type}] {step.text[:80]}")
        except Exception as e:
            await db.rollback()
            log.error(f"Episode creation failed: {e}")
            raise


if __name__ == "__main__":
    asyncio.run(main())
