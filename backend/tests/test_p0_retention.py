from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from contextedge.services.retention_service import apply_retention_policy


@pytest.mark.asyncio
async def test_retention_skips_legal_hold():
    tenant_id = uuid4()
    captured = {}
    active_item = SimpleNamespace(
        relevance_state="relevant",
        ingested_at=datetime.now(timezone.utc) - timedelta(days=45),
        evidence_type="message",
        canonical_entity_refs=None,
    )
    db_result = Mock()
    db_result.scalars.return_value.all.return_value = [active_item]

    async def execute(stmt):
        captured["stmt"] = stmt
        return db_result

    db = SimpleNamespace(execute=AsyncMock(side_effect=execute), flush=AsyncMock())

    archived = await apply_retention_policy(
        db,
        tenant_id=tenant_id,
        retention_days=30,
    )

    assert archived == 1
    assert active_item.relevance_state == "archived"
    assert any(value == "legal_hold" for value in captured["stmt"].compile().params.values())
    db.flush.assert_awaited_once()
