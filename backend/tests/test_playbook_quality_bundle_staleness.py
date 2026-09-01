"""Validator bundle retirement marks prior assessments stale before re-assess."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from contextedge.quality.registry import VALIDATOR_BUNDLE_VERSION
from contextedge.services.playbook_quality_service import (
    STALE_VALIDATOR_RETIRED,
    _invalidate_if_validator_bundle_retired,
)


@pytest.mark.asyncio
async def test_retires_assessment_when_bundle_version_differs():
    playbook = SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
    )
    prior = SimpleNamespace(
        validator_bundle_version="qa-2026.09.01-p3",
        superseded_at=None,
    )

    with patch(
        "contextedge.services.playbook_quality_service.latest_assessment",
        AsyncMock(return_value=prior),
    ), patch(
        "contextedge.services.playbook_quality_service.invalidate_assessments",
        AsyncMock(return_value=1),
    ) as invalidate:
        count = await _invalidate_if_validator_bundle_retired(AsyncMock(), playbook)

    assert count == 1
    invalidate.assert_awaited_once()
    assert invalidate.await_args.kwargs["reason"] == STALE_VALIDATOR_RETIRED


@pytest.mark.asyncio
async def test_skips_when_bundle_matches():
    playbook = SimpleNamespace(id=uuid.uuid4(), tenant_id=uuid.uuid4())
    prior = SimpleNamespace(
        validator_bundle_version=VALIDATOR_BUNDLE_VERSION,
        superseded_at=None,
    )

    with patch(
        "contextedge.services.playbook_quality_service.latest_assessment",
        AsyncMock(return_value=prior),
    ), patch(
        "contextedge.services.playbook_quality_service.invalidate_assessments",
        AsyncMock(),
    ) as invalidate:
        count = await _invalidate_if_validator_bundle_retired(AsyncMock(), playbook)

    assert count == 0
    invalidate.assert_not_awaited()
