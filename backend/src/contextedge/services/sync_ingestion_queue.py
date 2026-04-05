"""Queue normalization after raw evidence rows are committed."""

from __future__ import annotations

import uuid


def queue_normalize_raw_objects(raw_ids: list[uuid.UUID], tenant_id: uuid.UUID) -> None:
    """Enqueue Celery ``normalize_evidence`` for each raw object id (call only after DB commit)."""
    if not raw_ids:
        return
    from contextedge.workers.extraction_tasks import normalize_evidence

    tid = str(tenant_id)
    for rid in raw_ids:
        normalize_evidence.delay(str(rid), tid)
