"""Bind the current request/worker session to an MSP and client for RLS.

Two levels, two roles (SupportFlo plan D7/D8). ``app.msp_id`` is the MSP
(the hard boundary) and ``app.tenant_id`` is the client beneath it. Which
rows those admit is decided by the *role* the connection authenticated as,
not by a value this module sets:

    ce_app_msp     msp_id    = app.msp_id
    ce_app_client  msp_id    = app.msp_id AND tenant_id = app.tenant_id

That split is the point. The previous design admitted every row when
``app.bypass_rls`` was ``'on'`` — an application-controlled string, so any
code path that could execute SQL could turn isolation off, which is the
weakness D8 exists to avoid. Escalating from client scope to MSP scope now
requires different credentials, not a different value.

Three properties this module must not lose:

* ``set_config(..., is_local => true)`` — transaction-scoped. A bare ``SET``
  survives on a pooled connection and leaks scope into the next request.
* The value is a **bind parameter**, never concatenated into SQL.
* Empty means no access. ``current_setting(..., true)`` yields NULL when
  unset but ``''`` when set to empty; the policies use
  ``NULLIF(current_setting(...), '')::uuid`` so both collapse to NULL and
  ``col = NULL`` filters the row out. Failing closed is the default, not a
  case someone has to remember.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

INFO_MSP = "ce_rls_msp_id"
INFO_TENANT = "ce_rls_tenant_id"

_SET_SCOPE = text(
    "SELECT set_config('app.msp_id', :msp_id, true), "
    "       set_config('app.tenant_id', :tenant_id, true)"
)


def _params(msp_id: UUID | None, tenant_id: UUID | None) -> dict[str, str]:
    # str(None) would be the literal "None", which NULLIF does not catch and
    # ::uuid would reject at runtime. Empty string is the fail-closed value.
    return {
        "msp_id": str(msp_id) if msp_id else "",
        "tenant_id": str(tenant_id) if tenant_id else "",
    }


@event.listens_for(Session, "after_begin")
def _reapply_scope_after_begin(session, transaction, connection) -> None:
    """Re-apply scope on every new transaction.

    ``set_config(..., is_local => true)`` is undone at commit, so without this
    the second transaction on a session would run unscoped — which, because
    the policies fail closed, surfaces as mysteriously empty results rather
    than as a leak. Correct either way, but only this makes it work.
    """
    if INFO_MSP not in session.info and INFO_TENANT not in session.info:
        return
    connection.execute(
        _SET_SCOPE,
        _params(session.info.get(INFO_MSP), session.info.get(INFO_TENANT)),
    )


async def bind_session_scope(
    session: AsyncSession,
    *,
    msp_id: UUID | None,
    tenant_id: UUID | None,
) -> None:
    """Scope this session to one MSP and (optionally) one client.

    ``tenant_id=None`` under the MSP role means "every client of this MSP" —
    an aggregate report, a cross-client sweep. Under the client role it means
    no access, because that role's policy requires both keys.
    """
    sync_session = getattr(session, "sync_session", None)
    if sync_session is not None and isinstance(getattr(sync_session, "info", None), dict):
        sync_session.info[INFO_MSP] = msp_id
        sync_session.info[INFO_TENANT] = tenant_id
    await session.execute(_SET_SCOPE, _params(msp_id, tenant_id))


async def bind_session_tenant(
    session: AsyncSession,
    tenant_id: UUID | None,
    *,
    bypass: bool = False,
    msp_id: UUID | None = None,
) -> None:
    """Backwards-compatible shim for the pre-MSP call sites.

    ``bypass`` is accepted and **ignored**: there is no longer a value that
    turns the policy off, which is the entire point of D8. It is not removed
    from the signature yet because ~8 call sites pass it, and a silent
    behaviour change is easier to review than a rename that also moves code.
    Those call sites are migrated to ``bind_session_scope`` in this branch;
    the shim stays one release for anything out of tree.
    """
    await bind_session_scope(session, msp_id=msp_id, tenant_id=tenant_id)

async def resolve_msp_for_tenant(session: AsyncSession, tenant_id: UUID | None) -> UUID | None:
    """The MSP a client belongs to, read from the tenant row.

    Deliberately not taken from a JWT claim or a request header. Those are
    attacker-influenced, and this is the value the MSP predicate compares
    against — trusting the caller for it would make the boundary advisory.

    Returns None for an unmapped tenant, which the policies treat as no
    access. That is the correct outcome for a row `0097` could not backfill.
    """
    if tenant_id is None:
        return None
    from sqlalchemy import select

    from contextedge.models.tenant import Tenant

    result = await session.execute(select(Tenant.msp_id).where(Tenant.id == tenant_id))
    return result.scalar_one_or_none()
