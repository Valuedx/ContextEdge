import asyncio
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path

import redis.asyncio as aioredis
import structlog
from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from prometheus_fastapi_instrumentator import Instrumentator

from contextedge.config import settings
from contextedge.services.object_store import ensure_bucket

structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        (
            structlog.dev.ConsoleRenderer()
            if settings.app_debug
            else structlog.processors.JSONRenderer()
        ),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(settings.app_log_level.upper()),
)

logger = structlog.get_logger()


def _cors_origins() -> list[str]:
    origins: list[str] = []

    for value in [settings.app_cors_origins, settings.frontend_url]:
        for origin in value.split(","):
            normalized = origin.strip()
            if normalized and normalized not in origins:
                origins.append(normalized)

    return origins or ["http://localhost:3000", "http://127.0.0.1:3000"]


@asynccontextmanager
async def lifespan(app: FastAPI):
    import anyio
    app.state.redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    app.state.object_store_ok = False
    try:
        # ensure_bucket is synchronous (boto3), offload to thread to prevent event loop blocking
        await anyio.to_thread.run_sync(ensure_bucket)
        app.state.object_store_ok = True
        logger.info("object_store_bucket_ready", bucket=settings.minio_bucket)
    except Exception as exc:
        logger.warning("object_store_bucket_check_failed", error=str(exc))
    logger.info("startup", database=settings.database_url.split("@")[-1])
    yield
    await app.state.redis.close()
    logger.info("shutdown")


@lru_cache
def _expected_migration_head() -> str | None:
    """Head revision of the bundled alembic scripts; None if unavailable
    (e.g. installed without the alembic directory)."""
    try:
        from alembic.script import ScriptDirectory

        import contextedge

        alembic_dir = Path(contextedge.__file__).resolve().parents[2] / "alembic"
        if not alembic_dir.is_dir():
            return None
        return ScriptDirectory(str(alembic_dir)).get_current_head()
    except Exception:
        return None


async def _check_database() -> str:
    from sqlalchemy import text

    from contextedge.database import async_session_factory

    async with async_session_factory() as db:
        await db.execute(text("SELECT 1"))
    return "ok"


async def _check_migrations() -> str:
    expected = _expected_migration_head()
    if expected is None:
        return "unknown"

    from sqlalchemy import text

    from contextedge.database import async_session_factory

    async with async_session_factory() as db:
        row = (await db.execute(text("SELECT version_num FROM alembic_version"))).first()
    current = row[0] if row else None
    if current != expected:
        raise RuntimeError(f"database at {current!r}, code expects {expected!r}")
    return "ok"


def create_app() -> FastAPI:
    app = FastAPI(
        title="ContextEdge",
        description="Operational Memory and Living Playbook Platform",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
    )

    from contextedge.middleware.request_audit import RequestAuditMiddleware
    from contextedge.middleware.request_context import TenantContextMiddleware

    app.add_middleware(RequestAuditMiddleware)
    app.add_middleware(TenantContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins(),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(Exception)
    async def global_exception_handler(request, exc):
        logger.error("unhandled_exception", error=str(exc), path=request.url.path)
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=500,
            content={"detail": str(exc)},
        )

    Instrumentator().instrument(app).expose(app)

    from contextedge.api.v1 import router as api_v1_router
    app.include_router(api_v1_router, prefix="/api/v1")

    @app.get("/health")
    async def health():
        # Pure liveness: the process is up and serving. Dependency checks
        # live in /ready so a database blip doesn't get the process killed.
        return {"status": "healthy"}

    @app.get("/ready")
    async def ready(response: Response):
        """Readiness: DB reachable, migrations at head, Redis reachable.

        Object storage is reported but does not gate readiness — blob
        access is lazy and most request paths work without it.
        """

        async def _check_redis() -> str:
            await app.state.redis.ping()
            return "ok"

        checks: dict[str, str] = {}
        failed = False
        for name, check in (
            ("database", _check_database),
            ("migrations", _check_migrations),
            ("redis", _check_redis),
        ):
            try:
                checks[name] = await asyncio.wait_for(check(), timeout=5.0)
            except Exception as exc:
                checks[name] = f"error: {type(exc).__name__}"
                failed = True

        checks["object_store"] = (
            "ok" if getattr(app.state, "object_store_ok", False) else "degraded"
        )
        if failed:
            response.status_code = 503
            return {"status": "not_ready", "checks": checks}
        return {"status": "ready", "checks": checks}

    return app


app = create_app()
