from contextlib import asynccontextmanager

import redis.asyncio as aioredis
import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_fastapi_instrumentator import Instrumentator

from contextedge.config import settings

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


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.redis = aioredis.from_url(settings.redis_url, decode_responses=True)
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

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.app_cors_origins.split(","),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    from contextedge.middleware.request_audit import RequestAuditMiddleware
    from contextedge.middleware.request_context import TenantContextMiddleware

    app.add_middleware(RequestAuditMiddleware)
    app.add_middleware(TenantContextMiddleware)

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
