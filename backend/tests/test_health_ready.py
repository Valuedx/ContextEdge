from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

import contextedge.main as main_module


@pytest.fixture
def app(monkeypatch):
    application = main_module.create_app()
    # Simulate post-lifespan state without real Redis/MinIO.
    application.state.redis = AsyncMock()
    application.state.redis.ping = AsyncMock(return_value=True)
    application.state.object_store_ok = True
    return application


async def _get(app, path):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(path)


@pytest.mark.asyncio
async def test_health_is_pure_liveness(app):
    response = await _get(app, "/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}


@pytest.mark.asyncio
async def test_ready_reports_ready_when_dependencies_ok(app, monkeypatch):
    monkeypatch.setattr(main_module, "_check_database", AsyncMock(return_value="ok"))
    monkeypatch.setattr(main_module, "_check_migrations", AsyncMock(return_value="ok"))

    response = await _get(app, "/ready")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["checks"]["database"] == "ok"
    assert body["checks"]["migrations"] == "ok"
    assert body["checks"]["redis"] == "ok"


@pytest.mark.asyncio
async def test_ready_returns_503_when_database_down(app, monkeypatch):
    monkeypatch.setattr(
        main_module,
        "_check_database",
        AsyncMock(side_effect=ConnectionError("db down")),
    )
    monkeypatch.setattr(main_module, "_check_migrations", AsyncMock(return_value="ok"))

    response = await _get(app, "/ready")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    assert body["checks"]["database"].startswith("error:")


@pytest.mark.asyncio
async def test_ready_returns_503_on_migration_mismatch(app, monkeypatch):
    monkeypatch.setattr(main_module, "_check_database", AsyncMock(return_value="ok"))
    monkeypatch.setattr(
        main_module,
        "_check_migrations",
        AsyncMock(side_effect=RuntimeError("database at 0030, code expects 0032")),
    )

    response = await _get(app, "/ready")
    assert response.status_code == 503


@pytest.mark.asyncio
async def test_ready_object_store_degraded_does_not_gate(app, monkeypatch):
    monkeypatch.setattr(main_module, "_check_database", AsyncMock(return_value="ok"))
    monkeypatch.setattr(main_module, "_check_migrations", AsyncMock(return_value="ok"))
    app.state.object_store_ok = False

    response = await _get(app, "/ready")

    assert response.status_code == 200
    assert response.json()["checks"]["object_store"] == "degraded"


def test_expected_migration_head_resolves():
    """The alembic chain must resolve to exactly one head.

    Asserts the *invariant* rather than a pinned revision id. Pinning it
    meant every new migration broke this test, which is exactly how it
    came to be red — `0049_evidence_chunks_updated_at` landed while the
    constant still read `0048_fleet_groups`, so a test guarding the
    readiness probe was failing for a reason that had nothing to do with
    readiness. A stale assertion that fires on healthy changes trains
    people to ignore it.

    What actually matters here is what `/ready` depends on: the bundled
    scripts resolve, and they resolve to ONE head. Two heads mean a
    branched chain, which makes "is this database up to date" unanswerable.
    """
    main_module._expected_migration_head.cache_clear()
    head = main_module._expected_migration_head()
    assert head is not None, "alembic scripts must resolve in the test layout"

    from alembic.script import ScriptDirectory

    import contextedge

    alembic_dir = Path(contextedge.__file__).resolve().parents[2] / "alembic"
    script = ScriptDirectory(str(alembic_dir))

    heads = script.get_heads()
    assert len(heads) == 1, f"alembic chain has branched: {heads}"
    assert head == heads[0]


def test_every_migration_file_is_reachable_from_the_head():
    """A migration file that is not wired into the chain never runs.

    This is the failure the pinned-revision assertion was groping at: a
    new migration is added, but its `down_revision` does not extend the
    current head, so alembic silently skips it and the column it was
    meant to add never exists in production. Walking the chain from the
    head catches that; comparing a hardcoded string does not.
    """
    from alembic.script import ScriptDirectory

    import contextedge

    alembic_dir = Path(contextedge.__file__).resolve().parents[2] / "alembic"
    script = ScriptDirectory(str(alembic_dir))

    on_disk = {rev.revision for rev in script.walk_revisions()}
    reachable = {
        rev.revision
        for rev in script.iterate_revisions(script.get_current_head(), "base")
    }
    orphaned = on_disk - reachable
    assert not orphaned, f"migrations not reachable from the head: {sorted(orphaned)}"


@pytest.mark.asyncio
async def test_unhandled_exceptions_do_not_leak_details(app):
    """The global handler must return a generic message — raw exception
    text leaks SQL fragments, paths, and provider internals."""

    @app.get("/boom")
    async def boom():
        raise RuntimeError("secret: postgresql://user:hunter2@db/prod at /etc/app.py")

    # Starlette's ServerErrorMiddleware re-raises after sending the handler's
    # response; keep the transport from surfacing that re-raise so we can
    # assert on what the CLIENT actually received.
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/boom")

    assert response.status_code == 500
    body = response.json()
    assert body["detail"] == "Internal server error"
    assert "hunter2" not in response.text
    assert "postgresql" not in response.text
    assert "request_id" in body
