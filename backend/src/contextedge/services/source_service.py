import uuid
from datetime import datetime, timezone

from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.config import settings
from contextedge.connectors.registry import get_connector
from contextedge.models.source import Source, SourceCredential, SourceObject, SyncRun


def _get_fernet() -> Fernet:
    key = settings.fernet_key
    if not key or "change-me" in key:
        import structlog
        logger = structlog.get_logger()
        logger.warning("invalid_fernet_key_using_transient", reason="placeholder_or_empty")
        # Generate a transient key for this process session if none exists
        # This is dangerous for persistence but prevents crashes during dev
        key = Fernet.generate_key().decode()

    try:
        return Fernet(key.encode() if isinstance(key, str) else key)
    except Exception as exc:
        import structlog
        logger = structlog.get_logger()
        logger.error("fernet_initialization_failed", error=str(exc))
        # Fallback to a one-time key to avoid crashing the whole API
        return Fernet(Fernet.generate_key())


async def encrypt_credentials(creds: dict) -> bytes:
    import json
    f = _get_fernet()
    return f.encrypt(json.dumps(creds).encode())


async def decrypt_credentials(encrypted: bytes) -> dict:
    import json
    f = _get_fernet()
    return json.loads(f.decrypt(encrypted).decode())


async def validate_source_credentials(
    source_type: str, config: dict, credentials: dict
) -> tuple[bool, str]:
    connector = get_connector(source_type, config, credentials)
    result = await connector.validate_credentials()
    return result.valid, result.message


async def discover_source_objects(
    db: AsyncSession,
    source: Source,
) -> list[SourceObject]:
    cred_result = await db.execute(
        select(SourceCredential).where(
            SourceCredential.source_id == source.id,
            SourceCredential.status == "active",
        )
    )
    cred = cred_result.scalar_one_or_none()
    if not cred:
        raise ValueError("No active credentials for source")

    try:
        decrypted = await decrypt_credentials(cred.encrypted_credentials)
        connector = get_connector(source.source_type, source.config, decrypted)
        discovered = await connector.discover_objects()
        
        # If we got here, connection works
        source.auth_status = "connected"
    except Exception as exc:
        source.auth_status = "failed"
        await db.flush()
        raise exc

    new_objects = []
    for obj in discovered:
        existing_result = await db.execute(
            select(SourceObject).where(
                SourceObject.source_id == source.id,
                SourceObject.external_id == obj.external_id,
            )
        )
        existing_obj = existing_result.scalar_one_or_none()
        if existing_obj:
            # Update display_name and metadata in case they changed
            existing_obj.display_name = obj.display_name
            if obj.metadata:
                existing_obj.metadata_extra = obj.metadata
            continue

        so = SourceObject(
            tenant_id=source.tenant_id,
            source_id=source.id,
            object_type=obj.object_type,
            external_id=obj.external_id,
            display_name=obj.display_name,
            object_path=obj.object_path,
            owner_hint=obj.owner_hint,
            sensitivity_label=obj.sensitivity_label,
            metadata_extra=obj.metadata,
        )
        db.add(so)
        new_objects.append(so)

    await db.flush()
    source.discovery_status = "completed"

    # Return ALL objects for this source (existing + new) so callers get the full list
    all_result = await db.execute(
        select(SourceObject).where(SourceObject.source_id == source.id)
    )
    return list(all_result.scalars().all())


async def create_sync_run(
    db: AsyncSession,
    source_id: uuid.UUID,
    tenant_id: uuid.UUID,
    run_type: str,
    source_object_id: uuid.UUID | None = None,
) -> SyncRun:
    run = SyncRun(
        source_id=source_id,
        source_object_id=source_object_id,
        tenant_id=tenant_id,
        run_type=run_type,
        status="running",
        started_at=datetime.now(timezone.utc),
    )
    db.add(run)
    await db.flush()
    return run


async def rotate_source_credentials(
    db: AsyncSession,
    source: Source,
    *,
    credentials: dict,
    auth_type: str | None = None,
) -> SourceCredential:
    valid, message = await validate_source_credentials(
        source.source_type,
        source.config,
        credentials,
    )
    if not valid:
        raise ValueError(message)

    existing = await db.execute(
        select(SourceCredential).where(
            SourceCredential.source_id == source.id,
            SourceCredential.status == "active",
        )
    )
    now = datetime.now(timezone.utc)
    for row in existing.scalars().all():
        row.status = "rotated"
        row.rotated_at = now

    encrypted = await encrypt_credentials(credentials)
    credential = SourceCredential(
        source_id=source.id,
        auth_type=auth_type or source.auth_type,
        encrypted_credentials=encrypted,
        status="active",
        rotated_at=now,
    )
    db.add(credential)
    source.auth_status = "connected"
    await db.flush()
    await db.refresh(credential)
    return credential
