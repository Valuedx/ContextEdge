import asyncio
import hashlib
import json
import logging
import uuid
from pathlib import Path

from sqlalchemy import select

from contextedge.database import async_session_factory
from contextedge.models.evidence import RawEvidenceObject
from contextedge.models.source import Source
from contextedge.models.tenant import Tenant, User
from contextedge.services.sync_ingestion_queue import queue_normalize_raw_objects

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DATA_DIR = Path("D:/ContextEdge/ContextEdge/data")
TENANT_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
ADMIN_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000010")

async def get_or_create_local_source(db):
    # Ensure tenant exists (it should if seed was run)
    tenant = await db.get(Tenant, TENANT_ID)
    if not tenant:
        logger.error(f"Tenant {TENANT_ID} not found. Please run seed script first.")
        return None

    # Ensure admin user exists
    admin = await db.get(User, ADMIN_USER_ID)
    if not admin:
        logger.error(f"Admin user {ADMIN_USER_ID} not found. Please run seed script first.")
        return None

    result = await db.execute(
        select(Source).where(
            Source.tenant_id == TENANT_ID,
            Source.source_type == "local_file"
        )
    )
    source = result.scalar_one_or_none()

    if not source:
        logger.info("Creating local_file source...")
        source = Source(
            id=uuid.uuid4(),
            tenant_id=TENANT_ID,
            source_type="local_file",
            display_name="Local Data Ingestion",
            owner_user_id=ADMIN_USER_ID,
            auth_type="none",
            auth_status="active",
            is_active=True
        )
        db.add(source)
        await db.flush()

    return source

async def ingest_files():
    async with async_session_factory() as db:
        source = await get_or_create_local_source(db)
        if not source:
            return

        new_raw_ids = []

        for file_path in DATA_DIR.glob("*.txt"):
            logger.info(f"Processing {file_path.name}...")

            with open(file_path, encoding="utf-8") as f:
                content = f.read()

            external_id = f"local://{file_path.name}"
            payload = {
                "body_text": content,
                "file_name": file_path.name,
                "evidence_type": "log" if "log" in file_path.name.lower() else "message",
                "_connector_source_type": "local_file",
                "_connector_object_type": "file"
            }

            canonical = json.dumps(
                {"external_id": external_id, "body": payload},
                sort_keys=True,
                default=str,
            )
            content_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

            # Check for duplicates
            result = await db.execute(
                select(RawEvidenceObject.id).where(
                    RawEvidenceObject.tenant_id == TENANT_ID,
                    RawEvidenceObject.source_id == source.id,
                    RawEvidenceObject.external_id == external_id,
                    RawEvidenceObject.content_hash == content_hash,
                )
            )
            dup = result.scalar_one_or_none()

            if dup:
                logger.info(f"Existing item found: {file_path.name}")
                new_raw_ids.append(dup)
                continue

            raw = RawEvidenceObject(
                tenant_id=TENANT_ID,
                source_id=source.id,
                external_id=external_id,
                raw_payload=payload,
                content_hash=content_hash,
            )
            db.add(raw)
            await db.flush()
            new_raw_ids.append(raw.id)
            logger.info(f"Ingested {file_path.name} as {raw.id}")

        await db.commit()

        if new_raw_ids:
            logger.info(
                f"Enqueuing normalization for {len(new_raw_ids)} items (new and existing)..."
            )
            try:
                queue_normalize_raw_objects(new_raw_ids, TENANT_ID)
                logger.info("Successfully enqueued normalization tasks.")
            except Exception as e:
                logger.error(f"Failed to enqueue normalization: {e}")
        else:
            logger.info("No new items to process.")

if __name__ == "__main__":
    asyncio.run(ingest_files())
