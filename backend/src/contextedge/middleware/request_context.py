"""Decode JWT early for tenant context, request logging, and downstream guards."""

from __future__ import annotations

import uuid
from contextvars import ContextVar, Token
from typing import Any

from jose import JWTError, jwt
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from contextedge.config import settings
from contextedge.security_tokens import service_token_context

_REQUEST_CONTEXT: ContextVar[dict[str, Any] | None] = ContextVar(
    "contextedge_request_context",
    default=None,
)


def _parse_uuid(value: Any) -> uuid.UUID | None:
    if value in (None, ""):
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def bind_request_context(**values: Any) -> Token:
    current = dict(_REQUEST_CONTEXT.get() or {})
    for key, value in values.items():
        if value is None:
            continue
        current[key] = value
    return _REQUEST_CONTEXT.set(current)


def update_request_context(**values: Any) -> None:
    current = _REQUEST_CONTEXT.get()
    if current is None:
        current = {}
        _REQUEST_CONTEXT.set(current)
    for key, value in values.items():
        if value is None:
            continue
        current[key] = value


def reset_request_context(token: Token) -> None:
    _REQUEST_CONTEXT.reset(token)


def current_request_context() -> dict[str, Any]:
    return dict(_REQUEST_CONTEXT.get() or {})


def current_request_id() -> uuid.UUID | None:
    return _parse_uuid(current_request_context().get("request_id"))


def current_correlation_id() -> uuid.UUID | None:
    return _parse_uuid(current_request_context().get("correlation_id"))


def current_causation_id() -> uuid.UUID | None:
    return _parse_uuid(current_request_context().get("causation_id"))


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
        request_id = _parse_uuid(request.headers.get("x-request-id")) or uuid.uuid4()
        correlation_id = _parse_uuid(request.headers.get("x-correlation-id")) or request_id
        causation_id = _parse_uuid(request.headers.get("x-causation-id")) or request_id

        request.state.request_id = str(request_id)
        request.state.correlation_id = str(correlation_id)
        request.state.causation_id = str(causation_id)
        request.state.tenant_id = None
        request.state.user_id = None
        request.state.user_email = None
        request.state.roles = []

        token = bind_request_context(
            request_id=request_id,
            correlation_id=correlation_id,
            causation_id=causation_id,
        )

        try:
            path = request.url.path
            if path not in self.EXEMPT_PATHS and not any(
                path.startswith(prefix) for prefix in self.EXEMPT_PREFIXES
            ):
                auth = request.headers.get("authorization") or ""
                if auth.lower().startswith("bearer "):
                    bearer = auth.split(" ", 1)[1].strip()
                    try:
                        payload = jwt.decode(
                            bearer,
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

            update_request_context(
                tenant_id=_parse_uuid(request.state.tenant_id),
                user_id=_parse_uuid(request.state.user_id),
                user_email=request.state.user_email,
                roles=tuple(request.state.roles or []),
            )

            try:
                response = await call_next(request)
                response.headers.setdefault("X-Request-ID", str(request_id))
                response.headers.setdefault("X-Correlation-ID", str(correlation_id))
                return response
            except Exception:
                # Let global exception handlers handle it, but don't crash the middleware
                raise
        finally:
            reset_request_context(token)
