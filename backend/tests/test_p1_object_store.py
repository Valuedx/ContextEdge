import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import pytest

from contextedge.models.evidence import RawEvidenceObject
from contextedge.services.ingestion_persistence import (
    OFFLOAD_THRESHOLD_BYTES,
    persist_ingestion_events,
)
from contextedge.workers.extraction_tasks import _normalize


class _ScalarOneOrNoneResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeIngestionDb:
    def __init__(self):
        self.added = []
        self.execute = AsyncMock(return_value=_ScalarOneOrNoneResult(None))
        self.flush = AsyncMock(side_effect=self._flush)

    def add(self, obj):
        self.added.append(obj)

    async def _flush(self):
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = uuid4()


class _FakeNormalizeDb:
    def __init__(self, raw):
        self.raw = raw
        self.added = []
        self.get = AsyncMock(side_effect=self._get)
        self.execute = AsyncMock(return_value=_ScalarOneOrNoneResult(None))
        self.flush = AsyncMock(side_effect=self._flush)

    def add(self, obj):
        self.added.append(obj)

    async def _get(self, model, _ident):
        if model is RawEvidenceObject:
            return self.raw
        return None

    async def _flush(self):
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = uuid4()


@pytest.mark.asyncio
async def test_small_payload_stays_in_postgres():
    tenant_id = uuid4()
    source_id = uuid4()
    db = _FakeIngestionDb()
    event = SimpleNamespace(
        external_id="evt-1",
        content={"title": "Hello", "body": "small"},
        source_type="email",
        object_type="message",
        metadata={},
        thread_id=None,
        timestamp=None,
    )

    with patch("contextedge.services.ingestion_persistence.upload_raw") as upload_mock:
        created, skipped, new_raw_ids = await persist_ingestion_events(
            db,
            tenant_id=tenant_id,
            source_id=source_id,
            source_object_id=None,
            events=[event],
        )

    raw = db.added[0]
    assert created == 1
    assert skipped == 0
    assert new_raw_ids == [raw.id]
    assert raw.object_storage_key is None
    assert raw.raw_payload["body"] == "small"
    upload_mock.assert_not_called()


@pytest.mark.asyncio
async def test_large_payload_offloads_to_object_store():
    tenant_id = uuid4()
    source_id = uuid4()
    db = _FakeIngestionDb()
    event = SimpleNamespace(
        external_id="evt-2",
        content={"title": "Big", "body": "x" * (OFFLOAD_THRESHOLD_BYTES + 100)},
        source_type="slack",
        object_type="message",
        metadata={},
        thread_id=None,
        timestamp=None,
    )

    with patch(
        "contextedge.services.ingestion_persistence.upload_raw",
        return_value="raw/key.json",
    ) as upload_mock:
        created, skipped, new_raw_ids = await persist_ingestion_events(
            db,
            tenant_id=tenant_id,
            source_id=source_id,
            source_object_id=None,
            events=[event],
        )

    raw = db.added[0]
    assert created == 1
    assert skipped == 0
    assert new_raw_ids == [raw.id]
    assert raw.object_storage_key == "raw/key.json"
    assert raw.raw_payload["_offloaded"] is True
    upload_mock.assert_called_once()


@pytest.mark.asyncio
async def test_normalize_reads_offloaded_payload():
    tenant_id = uuid4()
    raw_id = uuid4()
    source_id = uuid4()
    payload = {"title": "Recovered", "body": "Loaded from object storage", "evidence_type": "kb"}
    raw = SimpleNamespace(
        id=raw_id,
        tenant_id=tenant_id,
        source_id=source_id,
        source_object_id=None,
        raw_payload={"_offloaded": True, "size_bytes": 12345},
        object_storage_key="raw/key.json",
    )
    db = _FakeNormalizeDb(raw)

    with (
        patch(
            "contextedge.services.artifact_extraction_service.download_raw",
            return_value=json.dumps(payload).encode("utf-8"),
        ),
        patch(
            "contextedge.workers.extraction_tasks.embed_evidence",
            AsyncMock(return_value=[0.1, 0.2]),
        ),
    ):
        result = await _normalize(db, str(raw_id), tenant_id)

    evidence = db.added[0]
    assert result["deduped"] is False
    assert result["evidence_id"] == str(evidence.id)
    assert evidence.title == "Recovered"
    assert evidence.body_text == "Loaded from object storage"
