"""C7 pattern domain audit: flags cross-domain members, never deletes."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from contextedge.services.pattern_audit_service import audit_pattern_domains


def _db(patterns, member_rows_by_pattern, events):
    async def execute(stmt):
        text = str(stmt)
        result = Mock()
        if text.startswith("SELECT patterns."):
            result.scalars.return_value.all.return_value = patterns
            return result
        if "pattern_evidence_links" in text:
            # One patterns list per call order — pop by pattern.
            result.all.return_value = member_rows_by_pattern.pop(0)
            return result
        result.scalars.return_value.all.return_value = []
        result.all.return_value = []
        return result

    return SimpleNamespace(execute=execute, add=lambda o: None, flush=AsyncMock())


@pytest.mark.asyncio
async def test_cross_domain_member_is_flagged_not_deleted(monkeypatch):
    tenant_id = uuid4()
    domain_a, domain_b = uuid4(), uuid4()
    pattern = SimpleNamespace(id=uuid4(), domain_id=domain_a)
    good_ep, bad_ep, global_ep = uuid4(), uuid4(), uuid4()
    events = []

    async def fake_event(db, **kwargs):
        events.append(kwargs)

    monkeypatch.setattr(
        "contextedge.services.event_log_service.append_operational_event", fake_event
    )
    db = _db(
        [pattern],
        [[(good_ep, domain_a), (bad_ep, domain_b), (global_ep, None)]],
        events,
    )

    result = await audit_pattern_domains(db, tenant_id)

    assert result["patterns_checked"] == 1
    assert result["patterns_flagged"] == 1
    assert result["violations"] == 1  # NULL-domain episode is never a violation
    assert result["flagged"][0]["violations"][0]["episode_id"] == str(bad_ep)
    assert events[0]["event_type"] == "pattern.domain_violation_flagged"


@pytest.mark.asyncio
async def test_clean_patterns_flag_nothing(monkeypatch):
    tenant_id = uuid4()
    domain_a = uuid4()
    pattern = SimpleNamespace(id=uuid4(), domain_id=domain_a)
    events = []

    async def fake_event(db, **kwargs):
        events.append(kwargs)

    monkeypatch.setattr(
        "contextedge.services.event_log_service.append_operational_event", fake_event
    )
    db = _db([pattern], [[(uuid4(), domain_a)]], events)

    result = await audit_pattern_domains(db, tenant_id)

    assert result["patterns_flagged"] == 0
    assert events == []
