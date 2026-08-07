"""Blueprint §1.6 primitive 2: cohort shared-attribute analysis."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from contextedge.services.cohort_service import get_cohort_shared_attributes


def _entity(**overrides):
    base = dict(
        id=uuid.uuid4(), entity_type="configuration_item", environment=None,
        manufacturer=None, model="TM-T88V", os_name=None, os_version=None,
        attributes={"ci_class": "cmdb_ci_printer"},
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _db(ci_ids, entities):
    db = MagicMock()
    ids_result = MagicMock()
    ids_result.scalars.return_value.all.return_value = ci_ids
    ent_result = MagicMock()
    ent_result.scalars.return_value.all.return_value = entities
    db.execute = AsyncMock(side_effect=[ids_result, ent_result])
    return db


@pytest.mark.asyncio
async def test_shared_model_is_found_and_ranked():
    """The S3 storyboard: 4 printers, same model, one on a different
    class — model coverage 100% leads, class 75% follows."""
    entities = [_entity() for _ in range(3)] + [_entity(attributes={"ci_class": "cmdb_ci_pos"})]
    db = _db([e.id for e in entities], entities)
    out = await get_cohort_shared_attributes(db, uuid.uuid4(), [uuid.uuid4()] * 4)
    dims = {s["dimension"]: s for s in out["shared"]}
    assert dims["model"]["coverage"] == 1.0
    assert dims["ci_class"]["value"] == "cmdb_ci_printer"
    assert out["shared"][0]["dimension"] == "model"


@pytest.mark.asyncio
async def test_below_cohort_floor_reports_nothing():
    """Two CIs sharing a model is coincidence, not a cohort — the
    honest answer is empty, never a stretched pattern."""
    entities = [_entity(), _entity()]
    db = _db([e.id for e in entities], entities)
    out = await get_cohort_shared_attributes(db, uuid.uuid4(), [uuid.uuid4()] * 2)
    assert out["ci_count"] == 2
    assert out["shared"] == []


@pytest.mark.asyncio
async def test_no_ci_links_is_safe():
    db = MagicMock()
    ids_result = MagicMock()
    ids_result.scalars.return_value.all.return_value = []
    db.execute = AsyncMock(return_value=ids_result)
    out = await get_cohort_shared_attributes(db, uuid.uuid4(), [uuid.uuid4()])
    assert out == {"cohort_size": 1, "ci_count": 0, "shared": []}


@pytest.mark.asyncio
async def test_empty_cohort_short_circuits():
    db = MagicMock()
    db.execute = AsyncMock()
    out = await get_cohort_shared_attributes(db, uuid.uuid4(), [])
    assert out["shared"] == []
    db.execute.assert_not_awaited()
