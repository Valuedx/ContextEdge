"""Negative tests for two-level RLS, on a real PostgreSQL.

SupportFlo plan D9 treats per-table-class negative tests as required rather
than optional, and it is right to: row-level security is a database-side
control and **cannot be asserted with a mock**. A fake session returns
whatever the test author believed the policy does, which is exactly the
belief under test.

What each test pins down:

* the fill trigger actually ran               — or everything below is vacuous
* a client sees only its own rows             — the core promise
* an MSP sees across its clients, not another's — the D7 asymmetry
* unset or empty scope sees nothing           — fails closed
* a client cannot write outside its scope     — WITH CHECK, not just USING
* the app roles do not hold BYPASSRLS         — the mechanism is not defeated
* every tenant-scoped table carries both policies — no table missed by the loop
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

BACKEND = Path(__file__).resolve().parents[1]

PLATFORM_ROLE = "ce_app_platform"
MSP_ROLE = "ce_app_msp"
CLIENT_ROLE = "ce_app_client"
ROLE_PASSWORD = "rls-test-only"  # noqa: S105 — ephemeral container, never a real secret


class AlembicUpgradeFailed(RuntimeError):
    """Raised so the fixture does not report a migration bug as a missing Docker."""


def _build_schema(sync_url: str) -> None:
    """Create the schema, then run `0097` on top of it.

    The migration chain **cannot build a fresh database**. `0001` runs
    `Base.metadata.create_all` against whatever the models declare today, so
    every later ADD COLUMN then fails with DuplicateColumn — the first is
    `0070`'s `episodes.ai_review`. That is a pre-existing defect, not one this
    branch introduced, and it is the reason nothing else in this suite runs
    against a real database.

    So: build the tables the way `0001` would, stamp the chain as applied
    through `0096`, and run `head` — which executes exactly `0097`. The
    migration under test is the real one, unmodified; only the route to a
    populated schema differs. `0097` is idempotent about its columns, so it
    behaves correctly whether or not `create_all` already made them.
    """
    import contextedge.models  # noqa: F401 - registers every mapper
    from contextedge.models.base import Base

    engine = sa.create_engine(sync_url)
    try:
        with engine.begin() as conn:
            conn.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector"))
            conn.execute(sa.text('CREATE EXTENSION IF NOT EXISTS "pgcrypto"'))
        Base.metadata.create_all(bind=engine)
    finally:
        engine.dispose()

    env = os.environ.copy()
    env["DATABASE_URL"] = sync_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    env["DATABASE_URL_SYNC"] = sync_url
    for args in (["stamp", "0096_clarification_regeneration"], ["upgrade", "head"]):
        proc = subprocess.run(  # noqa: S603
            [sys.executable, "-m", "alembic", *args],
            cwd=BACKEND,
            env=env,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise AlembicUpgradeFailed(
                f"alembic {' '.join(args)} failed ({proc.returncode}): {proc.stderr[-4000:]}"
            )


@pytest.fixture(scope="module")
def pg():
    """A migrated database with the app roles given LOGIN.

    `0097` creates the roles NOLOGIN on purpose — passwords do not belong in
    migrations. Granting LOGIN here is also a small proof that the operator
    step the migration documents is sufficient.
    """
    testcontainers = pytest.importorskip("testcontainers")
    if not hasattr(testcontainers, "postgres"):
        pytest.importorskip("testcontainers.postgres")
    from testcontainers.postgres import PostgresContainer

    try:
        # pgvector, not stock postgres: the chain runs `CREATE EXTENSION vector`.
        container = PostgresContainer("pgvector/pgvector:pg16")
        container.start()
    except Exception as exc:  # noqa: BLE001 — docker genuinely unavailable
        pytest.skip(f"PostgreSQL container unavailable: {exc}")

    try:
        sync_url = container.get_connection_url()
        # An AlembicUpgradeFailed here is a failure, never a skip.
        _build_schema(sync_url)
        owner = sa.create_engine(sync_url)
        with owner.begin() as conn:
            for role in (PLATFORM_ROLE, MSP_ROLE, CLIENT_ROLE):
                conn.execute(sa.text(f"ALTER ROLE {role} LOGIN PASSWORD '{ROLE_PASSWORD}'"))
        owner.dispose()
        yield sync_url
    finally:
        container.stop()


def _url_as(sync_url: str, role: str) -> str:
    """Same database, different credentials. The role IS the escalation."""
    tail = sync_url.split("@", 1)[1]
    return f"postgresql+asyncpg://{role}:{ROLE_PASSWORD}@{tail}"


@pytest.fixture(scope="module")
def world(pg):
    """Two MSPs, three clients, one audit row each.

    `audit_logs` is the probe table: tenant-scoped, no required foreign keys
    beyond the tenant, and one of the 43 that declared `tenant_id` directly
    rather than through the mixin — so it exercises the path most likely to
    have been missed.
    """
    ids = {k: uuid.uuid4() for k in ("msp_a", "msp_b", "c_a1", "c_a2", "c_b1")}
    owner = sa.create_engine(pg)
    with owner.begin() as conn:
        for key, name in (("msp_a", "MSP A"), ("msp_b", "MSP B")):
            conn.execute(
                sa.text(
                    "INSERT INTO msps (id, name, slug, config, is_active,"
                    " created_at, updated_at)"
                    " VALUES (:i, :n, :s, '{}'::jsonb, true, now(), now())"
                ),
                {"i": ids[key], "n": name, "s": f"{key}-{ids[key].hex[:8]}"},
            )
        for key, msp in (("c_a1", "msp_a"), ("c_a2", "msp_a"), ("c_b1", "msp_b")):
            conn.execute(
                sa.text(
                    "INSERT INTO tenants (id, msp_id, name, slug, config, is_active,"
                    " created_at, updated_at)"
                    " VALUES (:i, :m, :n, :s, '{}'::jsonb, true, now(), now())"
                ),
                {"i": ids[key], "m": ids[msp], "n": key, "s": f"{key}-{ids[key].hex[:8]}"},
            )
            # Deliberately no msp_id: the trigger must derive it.
            conn.execute(
                sa.text(
                    "INSERT INTO audit_logs"
                    " (id, tenant_id, action, resource_type, timestamp)"
                    " VALUES (:i, :t, :a, 'probe', now())"
                ),
                {"i": uuid.uuid4(), "t": ids[key], "a": f"probe-{key}"},
            )
    owner.dispose()
    return ids


async def _scoped_count(url: str, *, msp_id, tenant_id) -> int:
    engine = create_async_engine(url)
    try:
        async with AsyncSession(engine) as db:
            await db.execute(
                sa.text(
                    "SELECT set_config('app.msp_id', :m, true),"
                    " set_config('app.tenant_id', :t, true)"
                ),
                {
                    "m": str(msp_id) if msp_id else "",
                    "t": str(tenant_id) if tenant_id else "",
                },
            )
            res = await db.execute(
                sa.text("SELECT count(*) FROM audit_logs WHERE action LIKE 'probe-%'")
            )
            return int(res.scalar_one())
    finally:
        await engine.dispose()


@pytest.mark.integration
def test_the_trigger_backfilled_msp_id(pg, world):
    """`ce_fill_msp_id` must derive msp_id from the tenant on insert.

    The fixture inserts audit rows WITHOUT msp_id. If the trigger does not
    fire every row is NULL, every policy filters it out, and the isolation
    tests below would pass for entirely the wrong reason — an empty table
    isolates perfectly.
    """
    engine = sa.create_engine(pg)
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                sa.text(
                    "SELECT action, msp_id FROM audit_logs"
                    " WHERE action LIKE 'probe-%' ORDER BY action"
                )
            ).all()
        assert len(rows) == 3, "fixture should have inserted three probe rows"
        assert all(r[1] is not None for r in rows), f"trigger left msp_id NULL: {rows}"
    finally:
        engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_client_sees_only_its_own_rows(pg, world):
    n = await _scoped_count(
        _url_as(pg, CLIENT_ROLE), msp_id=world["msp_a"], tenant_id=world["c_a1"]
    )
    assert n == 1, "the client role must see exactly its own row"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_client_cannot_see_a_sibling_client_of_the_same_msp(pg, world):
    """The asymmetry D7 names: sharing an MSP is not shared visibility."""
    n = await _scoped_count(
        _url_as(pg, CLIENT_ROLE), msp_id=world["msp_a"], tenant_id=world["c_a2"]
    )
    assert n == 1


@pytest.mark.integration
@pytest.mark.asyncio
async def test_msp_sees_across_its_own_clients(pg, world):
    n = await _scoped_count(_url_as(pg, MSP_ROLE), msp_id=world["msp_a"], tenant_id=None)
    assert n == 2, "MSP A has two clients and must see both"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_msp_cannot_see_another_msps_clients(pg, world):
    """The hard boundary. If one assertion in this file matters, it is this."""
    n = await _scoped_count(_url_as(pg, MSP_ROLE), msp_id=world["msp_b"], tenant_id=None)
    assert n == 1, "MSP B must see only its own client, never MSP A's two"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_a_client_scope_does_not_narrow_the_msp_role(pg, world):
    """The MSP policy ignores tenant_id, so passing one must not imply confinement.

    A false sense of narrowing is worse than none: a caller that believes it
    scoped down will write code that assumes it.
    """
    n = await _scoped_count(
        _url_as(pg, MSP_ROLE), msp_id=world["msp_a"], tenant_id=world["c_a1"]
    )
    assert n == 2, "the MSP role still sees both clients even with a tenant bound"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_unset_scope_sees_nothing(pg, world):
    """Empty means no access, never a wildcard.

    The dangerous failure is the opposite: a policy treating an unset GUC as
    "no filter" turns a forgotten bind into a full-corpus read.
    """
    for role in (CLIENT_ROLE, MSP_ROLE):
        assert await _scoped_count(_url_as(pg, role), msp_id=None, tenant_id=None) == 0
        assert (
            await _scoped_count(_url_as(pg, role), msp_id=None, tenant_id=world["c_a1"])
        ) == 0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_client_cannot_write_outside_its_scope(pg, world):
    """WITH CHECK, not only USING.

    A policy with USING alone lets a caller INSERT rows it then cannot read —
    data written into another tenant's partition, invisible to the writer.
    """
    engine = create_async_engine(_url_as(pg, CLIENT_ROLE))
    try:
        async with AsyncSession(engine) as db:
            await db.execute(
                sa.text(
                    "SELECT set_config('app.msp_id', :m, true),"
                    " set_config('app.tenant_id', :t, true)"
                ),
                {"m": str(world["msp_a"]), "t": str(world["c_a1"])},
            )
            with pytest.raises(Exception) as caught:
                await db.execute(
                    sa.text(
                        "INSERT INTO audit_logs"
                        " (id, tenant_id, action, resource_type, timestamp)"
                        " VALUES (:i, :t, 'smuggled', 'probe', now())"
                    ),
                    {"i": uuid.uuid4(), "t": world["c_a2"]},
                )
            assert "policy" in str(caught.value).lower()
    finally:
        await engine.dispose()


@pytest.mark.integration
def test_application_roles_do_not_hold_bypassrls(pg):
    """BYPASSRLS defeats the mechanism entirely and silently.

    Asserted rather than assumed because it is one ALTER away and nothing
    behaves differently until an isolation test stops failing for the wrong
    reason.
    """
    engine = sa.create_engine(pg)
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                sa.text(
                    "SELECT rolname, rolbypassrls FROM pg_roles"
                    " WHERE rolname IN (:a, :b, :c)"
                ),
                {"a": MSP_ROLE, "b": CLIENT_ROLE, "c": PLATFORM_ROLE},
            ).all()
        assert len(rows) == 3, f"all three app roles must exist, got {rows}"
        for name, bypass in rows:
            assert bypass is False, f"{name} holds BYPASSRLS"
    finally:
        engine.dispose()


@pytest.mark.integration
def test_every_tenant_scoped_table_carries_both_policies(pg):
    """No table escapes `0097`'s loop.

    A new tenant-scoped table added without `msp_id` gets no policy and no
    error — it is simply unprotected. This is what makes that loud.
    """
    engine = sa.create_engine(pg)
    try:
        with engine.connect() as conn:
            scoped = {
                r[0]
                for r in conn.execute(
                    sa.text(
                        "SELECT c.table_name FROM information_schema.columns c"
                        " JOIN information_schema.tables t"
                        "   ON t.table_schema=c.table_schema AND t.table_name=c.table_name"
                        " WHERE c.table_schema='public' AND c.column_name='tenant_id'"
                        "   AND c.table_name <> 'tenants' AND t.table_type='BASE TABLE'"
                    )
                )
            }
            policed = {
                (r[0], r[1])
                for r in conn.execute(
                    sa.text(
                        "SELECT tablename, policyname FROM pg_policies"
                        " WHERE schemaname='public'"
                    )
                )
            }
            forced = {
                r[0]
                for r in conn.execute(
                    sa.text(
                        "SELECT relname FROM pg_class"
                        " WHERE relrowsecurity AND relforcerowsecurity"
                    )
                )
            }
        missing_policy = sorted(
            t
            for t in scoped
            if (t, "ce_msp_isolation") not in policed
            or (t, "ce_client_isolation") not in policed
        )
        assert not missing_policy, (
            f"tenant-scoped tables with no MSP/client policy: {missing_policy}"
        )
        missing_force = sorted(scoped - forced)
        assert not missing_force, f"tables without FORCE ROW LEVEL SECURITY: {missing_force}"
        legacy = sorted(t for t, p in policed if p == "tenant_isolation")
        assert not legacy, f"0078's bypassable policy still present on: {legacy}"
    finally:
        engine.dispose()

@pytest.mark.integration
@pytest.mark.asyncio
async def test_a_pooled_connection_does_not_leak_scope_between_uses(pg, world):
    """The risk that arrived with connection pooling.

    Under NullPool every task got a brand-new connection, so scope could not
    survive between tasks by construction. Connections are reused now, so
    two things have to hold, and they are different mechanisms:

    1. `bind_session_scope` uses `set_config(..., is_local => true)`, which
       reverts at COMMIT. That is the primary guarantee and it is what this
       first half exercises — note the engine here is a plain one with no
       event hooks, so a pass proves transaction-locality alone.
    2. A SESSION-level GUC (`is_local => false`) would persist on the
       connection instead. Nothing in the app sets one today, but the
       downgrade path and any future raw `SET` would; the checkin/checkout
       hooks on `create_db_engine` are the backstop, covered below.

    Because the MSP policy needs only msp_id, a leaked msp_id alone is enough
    to leak data — which is why both halves matter.
    """
    from sqlalchemy.ext.asyncio import create_async_engine as _cae

    engine = _cae(_url_as(pg, MSP_ROLE), pool_size=1, max_overflow=0)
    try:
        async with AsyncSession(engine) as db:
            await db.execute(
                sa.text(
                    "SELECT set_config('app.msp_id', :m, true),"
                    " set_config('app.tenant_id', '', true)"
                ),
                {"m": str(world["msp_a"])},
            )
            first = (
                await db.execute(
                    sa.text("SELECT count(*) FROM audit_logs WHERE action LIKE 'probe-%'")
                )
            ).scalar_one()
            await db.commit()
        assert first == 2, "sanity: MSP A must see its two clients on the first use"

        # Same pooled connection, nothing bound. Must see nothing.
        async with AsyncSession(engine) as db:
            second = (
                await db.execute(
                    sa.text("SELECT count(*) FROM audit_logs WHERE action LIKE 'probe-%'")
                )
            ).scalar_one()
        assert second == 0, (
            "a pooled connection carried scope into the next use: "
            "the checkin/checkout hooks are not clearing app.msp_id"
        )
    finally:
        await engine.dispose()

@pytest.mark.integration
@pytest.mark.asyncio
async def test_the_pool_hooks_clear_a_session_level_scope(pg, world):
    """The backstop, exercised directly.

    `create_db_engine` attaches checkout/checkin handlers that wipe both scope
    GUCs. This sets them SESSION-level — the one form transaction-locality
    does not undo — returns the connection, and checks the next checkout of
    the same single-connection pool starts clean.
    """
    from sqlalchemy import event as sa_event
    from sqlalchemy.ext.asyncio import create_async_engine as _cae

    from contextedge.database import _reset_rls_gucs_checkin, _reset_rls_gucs_checkout

    engine = _cae(_url_as(pg, MSP_ROLE), pool_size=1, max_overflow=0)
    sa_event.listen(engine.sync_engine, "checkout", _reset_rls_gucs_checkout)
    sa_event.listen(engine.sync_engine, "checkin", _reset_rls_gucs_checkin)
    try:
        async with AsyncSession(engine) as db:
            await db.execute(
                sa.text("SELECT set_config('app.msp_id', :m, false)"),
                {"m": str(world["msp_a"])},
            )
            leaked = (
                await db.execute(
                    sa.text("SELECT count(*) FROM audit_logs WHERE action LIKE 'probe-%'")
                )
            ).scalar_one()
            assert leaked == 2, "sanity: a session-level GUC does scope the connection"
            await db.commit()

        async with AsyncSession(engine) as db:
            after = (
                await db.execute(
                    sa.text("SELECT count(*) FROM audit_logs WHERE action LIKE 'probe-%'")
                )
            ).scalar_one()
        assert after == 0, (
            "the pool hooks did not clear a session-level app.msp_id; "
            "a reused connection would serve the previous MSP's rows"
        )
    finally:
        await engine.dispose()

@pytest.mark.integration
@pytest.mark.asyncio
async def test_scope_survives_a_released_connection(pg, world):
    """`released` must not silently widen or drop scope.

    The whole point of committing before an external call is to stop holding
    a connection across it. That only works if the scope comes back on the
    next transaction — otherwise the code after the call reads an empty
    database (fail-closed, so it looks like missing data, not a leak) or,
    worse, a wider one.

    `bind_session_scope` records the keys on `session.info` and the
    `Session.after_begin` hook re-applies them. This drives a real commit
    between two reads to prove that hook fires.
    """
    from contextedge.db.external_call import released
    from contextedge.tenant_rls import bind_session_scope

    engine = create_async_engine(_url_as(pg, CLIENT_ROLE))
    try:
        async with AsyncSession(engine) as db:
            await bind_session_scope(db, msp_id=world["msp_a"], tenant_id=world["c_a1"])
            before = (
                await db.execute(
                    sa.text("SELECT count(*) FROM audit_logs WHERE action LIKE 'probe-%'")
                )
            ).scalar_one()
            assert before == 1, "sanity: the client sees its own row before the gap"

            async with released(db, reason="test.external_call"):
                pass  # stands in for the LLM call

            after = (
                await db.execute(
                    sa.text("SELECT count(*) FROM audit_logs WHERE action LIKE 'probe-%'")
                )
            ).scalar_one()
        assert after == 1, (
            "scope did not survive the released connection: after_begin "
            f"did not re-bind (saw {after} rows, expected 1)"
        )
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_the_budget_advisory_lock_serialises_across_connections(pg, world):
    """Two separate connections must not hold the tenant budget lock at once.

    This is the property the asyncio.Lock never had. Held on connection A,
    connection B's attempt must block — asserted with a short timeout, since
    "it blocked" cannot be observed directly.
    """
    import asyncio as _asyncio

    from contextedge.services.tenant_budget_service import _advisory_key

    key = _advisory_key(world["c_a1"])
    ea = create_async_engine(_url_as(pg, MSP_ROLE))
    eb = create_async_engine(_url_as(pg, MSP_ROLE))
    try:
        async with AsyncSession(ea) as a:
            await a.execute(sa.text("SELECT pg_advisory_xact_lock(:k)"), {"k": key})

            async def contend():
                async with AsyncSession(eb) as b:
                    await b.execute(sa.text("SELECT pg_advisory_xact_lock(:k)"), {"k": key})

            with pytest.raises(_asyncio.TimeoutError):
                await _asyncio.wait_for(contend(), timeout=2.0)

            await a.rollback()  # releases the xact lock

        # Once released, the same key is immediately takeable.
        async with AsyncSession(eb) as b:
            await _asyncio.wait_for(
                b.execute(sa.text("SELECT pg_advisory_xact_lock(:k)"), {"k": key}),
                timeout=5.0,
            )
            await b.rollback()
    finally:
        await ea.dispose()
        await eb.dispose()
