"""Queue normalization after raw evidence rows are committed."""

from __future__ import annotations

import uuid


class NormalizeEnqueueError(RuntimeError):
    """Raised when one or more normalize tasks could not be enqueued."""

    def __init__(self, *, pending_raw_ids: list[uuid.UUID], detail: str):
        super().__init__(detail)
        self.pending_raw_ids = pending_raw_ids


def queue_normalize_raw_objects(raw_ids: list[uuid.UUID], tenant_id: uuid.UUID) -> None:
    """Enqueue Celery ``normalize_evidence`` for each raw object id (call only after DB commit)."""
    if not raw_ids:
        return
    from contextedge.workers.extraction_tasks import normalize_evidence

    tid = str(tenant_id)
    for index, rid in enumerate(raw_ids):
        try:
            normalize_evidence.delay(str(rid), tid)
        except Exception as exc:
            raise NormalizeEnqueueError(
                pending_raw_ids=raw_ids[index:],
                detail=str(exc),
            ) from exc
