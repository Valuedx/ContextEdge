"""Deletion guards on the evidence APIs.

An end-to-end review found two live defects here, and these tests pin the
fixes:

1. Bulk-delete deleted correlation edges and attachments for CALLER-SUPPLIED
   evidence ids before any tenant check — only the final EvidenceItem delete
   was tenant-scoped, so a caller could destroy another tenant's dependency
   rows by supplying foreign UUIDs.
2. None of the destructive routes honoured legal hold, so the one label that
   exists to make evidence undeletable did not survive the delete button.

The routes now resolve-and-authorize FIRST (the whole request fails on any
foreign id), refuse held items with 409, and purge preserves held evidence
plus its raw objects.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from contextedge.api.v1.evidence import bulk_delete_evidence, delete_evidence
from contextedge.schemas.evidence import EvidenceBulkDeleteRequest


def _user(tenant_id, roles=("domain_admin",)):
    def has_role(role):
        return role in roles

    def require_role(role):
        if not has_role(role):
            raise HTTPException(status_code=403, detail="forbidden")

    return SimpleNamespace(
        tenant_id=tenant_id,
        user_id=uuid4(),
        email="admin@test.local",
        roles=list(roles),
        has_role=has_role,
        require_role=require_role,
    )


def _rows_result(rows):
    from unittest.mock import Mock

    result = Mock()
    result.all.return_value = rows
    result.scalars.return_value.all.return_value = [r[0] for r in rows]
    return result


@pytest.mark.asyncio
async def test_bulk_delete_rejects_ids_outside_the_tenant():
    """A foreign id anywhere in the request fails the WHOLE request before
    any delete statement runs — partial application would read as success."""
    tenant = uuid4()
    mine, foreign = uuid4(), uuid4()

    # Resolution returns only the caller's row; the foreign id is absent.
    db = SimpleNamespace(
        execute=AsyncMock(return_value=_rows_result([(mine, None)])),
        commit=AsyncMock(),
    )

    with pytest.raises(HTTPException) as exc:
        await bulk_delete_evidence(
            EvidenceBulkDeleteRequest(ids=[mine, foreign]), db, _user(tenant)
        )
    assert exc.value.status_code == 404
    # Exactly one query ran: the resolve. No delete ever executed.
    assert db.execute.await_count == 1


@pytest.mark.asyncio
async def test_bulk_delete_refuses_legal_hold_with_409():
    tenant = uuid4()
    held = uuid4()
    db = SimpleNamespace(
        execute=AsyncMock(return_value=_rows_result([(held, "legal_hold")])),
        commit=AsyncMock(),
    )

    with pytest.raises(HTTPException) as exc:
        await bulk_delete_evidence(
            EvidenceBulkDeleteRequest(ids=[held]), db, _user(tenant)
        )
    assert exc.value.status_code == 409
    assert "legal hold" in exc.value.detail
    assert db.execute.await_count == 1  # resolve only, nothing deleted


@pytest.mark.asyncio
async def test_single_delete_refuses_legal_hold_with_409():
    tenant = uuid4()
    item = SimpleNamespace(sensitivity_label="legal_hold")
    result = SimpleNamespace(scalar_one_or_none=lambda: item)
    db = SimpleNamespace(execute=AsyncMock(return_value=result), commit=AsyncMock())

    with pytest.raises(HTTPException) as exc:
        await delete_evidence(uuid4(), db, _user(tenant))
    assert exc.value.status_code == 409
    assert "legal hold" in exc.value.detail
