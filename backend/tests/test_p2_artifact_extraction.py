from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from contextedge.models.evidence import AttachmentArtifact, EvidenceItem, RawEvidenceObject
from contextedge.services.artifact_extraction_service import extract_artifact_text, process_attachment_artifact
from contextedge.workers.extraction_tasks import _normalize


class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def scalar_one(self):
        return self._value


class _ScalarsListResult:
    def __init__(self, values):
        self._values = values

    def scalars(self):
        return self

    def all(self):
        return self._values


class _FakeNormalizeDb:
    def __init__(self, raw):
        self.raw = raw
        self.added = []
        self.get = AsyncMock(side_effect=self._get)
        self.execute = AsyncMock(side_effect=[_ScalarResult(None), _ScalarsListResult([])])
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


class _FakeArtifactDb:
    def __init__(self, artifact, evidence, raw):
        self.artifact = artifact
        self.evidence = evidence
        self.raw = raw
        self.get = AsyncMock(side_effect=self._get)
        self.execute = AsyncMock(
            side_effect=[
                _ScalarsListResult([artifact]),
                # synchronize_evidence_artifacts now re-chunks against the
                # merged body: attachment extraction runs after normalize,
                # so without this the attachment's text never reached
                # evidence_chunks at all. The call fails soft against this
                # fake — what matters here is that it consumes a slot.
                _ScalarsListResult([]),
                _ScalarResult(0),
            ]
        )
        self.flush = AsyncMock()

    async def _get(self, model, ident):
        if model is AttachmentArtifact and ident == self.artifact.id:
            return self.artifact
        if model is EvidenceItem and ident == self.evidence.id:
            return self.evidence
        if model is RawEvidenceObject and ident == self.raw.id:
            return self.raw
        return None


def test_extract_artifact_text_flattens_json_logs():
    result = extract_artifact_text(
        filename="vpn.json",
        mime_type="application/json",
        data=b'{"service":"vpn","status":"error","nested":{"code":500}}',
    )

    assert result.status == "completed"
    assert result.parser_type == "json_log"
    assert "service=vpn" in result.text
    assert "nested.code=500" in result.text


def test_extract_artifact_text_strips_transcript_timestamps():
    result = extract_artifact_text(
        filename="meeting.vtt",
        mime_type="text/vtt",
        data=(
            b"WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nAnalyst: Check VPN.\n\n"
            b"00:00:04.000 --> 00:00:05.000\nUser: It works now.\n"
        ),
    )

    assert result.status == "completed"
    assert result.parser_type == "transcript_text"
    assert "00:00:01.000" not in result.text
    assert "Analyst: Check VPN." in result.text
    assert "User: It works now." in result.text


@pytest.mark.asyncio
async def test_normalize_registers_attachment_artifacts():
    tenant_id = uuid4()
    raw_id = uuid4()
    source_id = uuid4()
    raw = SimpleNamespace(
        id=raw_id,
        tenant_id=tenant_id,
        source_id=source_id,
        source_object_id=None,
        raw_payload={
            "title": "Incident",
            "body": "Base evidence body",
            "evidence_type": "message",
            "attachments": [
                {"filename": "agent.log", "content": "line one\nline two", "content_type": "text/plain"},
                {"filename": "vpn.json", "content": {"service": "vpn", "status": "down"}, "content_type": "application/json"},
            ],
        },
        object_storage_key=None,
    )
    db = _FakeNormalizeDb(raw)

    with (
        patch("contextedge.workers.extraction_tasks.embed_evidence", AsyncMock(return_value=[0.1, 0.2])),
        patch("contextedge.workers.extraction_tasks.link_evidence_identities", AsyncMock(return_value=[])),
        patch(
            "contextedge.services.artifact_extraction_service.upload_artifact",
            side_effect=["artifacts/1", "artifacts/2"],
        ),
    ):
        result = await _normalize(db, str(raw_id), tenant_id)

    attachment_rows = [obj for obj in db.added if isinstance(obj, AttachmentArtifact)]
    assert result["deduped"] is False
    assert len(result["attachment_ids"]) == 2
    assert len(attachment_rows) == 2
    assert attachment_rows[0].filename == "agent.log"
    assert attachment_rows[1].filename == "vpn.json"


@pytest.mark.asyncio
async def test_process_attachment_artifact_merges_text_back_into_evidence():
    tenant_id = uuid4()
    raw_id = uuid4()
    evidence_id = uuid4()
    artifact_id = uuid4()
    source_id = uuid4()
    evidence = EvidenceItem(
        id=evidence_id,
        tenant_id=tenant_id,
        source_id=source_id,
        evidence_type="message",
        title="Incident",
        body_text="Base evidence body",
        raw_object_ref=raw_id,
        relevance_state="unclassified",
    )
    artifact = AttachmentArtifact(
        id=artifact_id,
        evidence_id=evidence_id,
        filename="agent.log",
        mime_type="text/plain",
        size_bytes=20,
        object_storage_key="artifacts/agent.log",
        extraction_status="pending",
    )
    raw = SimpleNamespace(
        id=raw_id,
        tenant_id=tenant_id,
        source_id=source_id,
        raw_payload={"title": "Incident", "body": "Base evidence body"},
        object_storage_key=None,
    )
    db = _FakeArtifactDb(artifact, evidence, raw)

    with (
        patch(
            "contextedge.services.artifact_extraction_service.download_artifact",
            return_value=b"line one\nline two",
        ),
        patch(
            "contextedge.services.artifact_extraction_service.embed_evidence",
            AsyncMock(return_value=[0.3, 0.4]),
        ),
        patch(
            "contextedge.services.artifact_extraction_service.link_evidence_identities",
            AsyncMock(return_value=[]),
        ),
        patch(
            "contextedge.services.artifact_extraction_service.append_operational_event",
            AsyncMock(),
        ),
    ):
        result = await process_attachment_artifact(
            db,
            artifact_id=artifact_id,
            tenant_id=tenant_id,
        )

    assert result["status"] == "completed"
    assert result["follow_up_ready"] is True
    assert artifact.parser_type == "log_text"
    assert "[Attachment: agent.log | parser=log_text]" in evidence.body_text
    assert "line one" in evidence.body_text
    assert evidence.embedding == [0.3, 0.4]
