"""Decode JWT early for tenant context, request logging, and downstream guards."""

from jose import JWTError, jwt
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from contextedge.config import settings
from contextedge.security_tokens import service_token_context


class TenantContextMiddleware(BaseHTTPMiddleware):
    """Populate `request.state` from Bearer JWT (same secret/algorithm as API auth)."""

    EXEMPT_PREFIXES = (
        "/health",
        "/ready",
        "/docs",
        "/redoc",
        "/openapi.json",
        "/metrics",
    )
    EXEMPT_PATHS = {"/api/v1/auth/login"}

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path
        if path in self.EXEMPT_PATHS or any(path.startswith(p) for p in self.EXEMPT_PREFIXES):
            return await call_next(request)

        request.state.tenant_id = None
        request.state.user_id = None
        request.state.user_email = None
        request.state.roles = []

        auth = request.headers.get("authorization") or ""
        if auth.lower().startswith("bearer "):
            token = auth.split(" ", 1)[1].strip()
            try:
                payload = jwt.decode(
                    token,
                    settings.jwt_secret_key,
                    algorithms=[settings.jwt_algorithm],
                )
                request.state.user_id = payload.get("sub")
                request.state.tenant_id = payload.get("tenant_id")
                request.state.user_email = payload.get("email")
                request.state.roles = payload.get("roles") or []
            except JWTError:
                pass

        svc = request.headers.get("x-service-token")
        if svc and request.state.tenant_id is None:
            ctx = service_token_context(svc.strip())
            if ctx:
                request.state.user_id = ctx["user_id"]
                request.state.tenant_id = ctx["tenant_id"]
                request.state.user_email = ctx["email"]
                request.state.roles = ctx["roles"]

        return await call_next(request)
