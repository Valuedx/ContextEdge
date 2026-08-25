"""Bind the current request/worker session to a tenant for Postgres RLS."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

INFO_TENANT = "ce_rls_tenant_id"
INFO_BYPASS = "ce_rls_bypass"


def _apply_gucs_on_connection(connection, tenant_id: UUID | None, bypass: bool) -> None:
    connection.execute(
        text("SELECT set_config('app.bypass_rls', :bypass, true)"),
        {"bypass": "on" if bypass else "off"},
    )
    connection.execute(
        text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
        {"tenant_id": str(tenant_id) if tenant_id else ""},
    )


@event.listens_for(Session, "after_begin")
def _reapply_rls_after_begin(session, transaction, connection) -> None:
    if INFO_BYPASS not in session.info and INFO_TENANT not in session.info:
        return
    _apply_gucs_on_connection(
        connection,
        session.info.get(INFO_TENANT),
        bool(session.info.get(INFO_BYPASS)),
    )


async def bind_session_tenant(
    session: AsyncSession,
    tenant_id: UUID | None,
    *,
    bypass: bool,
) -> None:
    sync_session = getattr(session, "sync_session", None)
    if sync_session is not None and hasattr(sync_session, "info") and isinstance(sync_session.info, dict):
        sync_session.info[INFO_TENANT] = tenant_id
        sync_session.info[INFO_BYPASS] = bypass
    await session.execute(
        text("SELECT set_config('app.bypass_rls', :bypass, true)"),
        {"bypass": "on" if bypass else "off"},
    )
    await session.execute(
        text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
        {"tenant_id": str(tenant_id) if tenant_id else ""},
    )
