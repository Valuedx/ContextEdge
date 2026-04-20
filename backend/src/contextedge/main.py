from contextlib import asynccontextmanager

import redis.asyncio as aioredis
import structlog
from fastapi import FastAPI
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
    try:
        # ensure_bucket is synchronous (boto3), offload to thread to prevent event loop blocking
        await anyio.to_thread.run_sync(ensure_bucket)
        logger.info("object_store_bucket_ready", bucket=settings.minio_bucket)
    except Exception as exc:
        logger.warning("object_store_bucket_check_failed", error=str(exc))
    logger.info("startup", database=settings.database_url.split("@")[-1])
    yield
    await app.state.redis.close()
    logger.info("shutdown")


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
        return {"status": "healthy"}

    @app.get("/ready")
    async def ready():
        return {"status": "ready"}

    return app


app = create_app()
