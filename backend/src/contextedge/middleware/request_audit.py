"""Best-effort audit trail for mutating API calls (complements explicit `log_audit_event` calls)."""

import json
import uuid

import structlog
from sqlalchemy import create_engine, text
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from contextedge.config import settings

logger = structlog.get_logger()
_sync_engine = None


def _engine():
    global _sync_engine
    if _sync_engine is None:
        _sync_engine = create_engine(settings.database_url_sync, pool_pre_ping=True)
    return _sync_engine


class RequestAuditMiddleware(BaseHTTPMiddleware):
    """Log mutating requests to structlog and insert a row into `audit_logs` when tenant is known."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        try:
            response = await call_next(request)
        except Exception:
            # Re-raise so global exception handlers can catch it
            # CORSMiddleware will still be able to add headers if this bubbles up correctly
            raise

        if request.method not in ("POST", "PATCH", "PUT", "DELETE"):
            return response
        if not request.url.path.startswith("/api/v1"):
            return response
        if request.url.path.startswith("/api/v1/auth/login"):
            return response

        tenant_id = getattr(request.state, "tenant_id", None)
        user_id = getattr(request.state, "user_id", None)
        email = getattr(request.state, "user_email", None)

        logger.info(
            "http.mutating_request",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            tenant_id=tenant_id,
            user_id=user_id,
            request_id=getattr(request.state, "request_id", None),
            correlation_id=getattr(request.state, "correlation_id", None),
        )

        # Denied (401/403) and failed mutating calls are audit-relevant too —
        # an attacker probing execution endpoints must appear in the trail,
        # not only their successes. Everything with a resolved tenant is
        # recorded; the outcome is distinguished by the `outcome` field.
        if tenant_id:
            import anyio
            try:
                tid = uuid.UUID(str(tenant_id))
                aid = uuid.UUID(str(user_id)) if user_id else None
                action = f"http.{request.method.lower()}.{request.url.path.strip('/').replace('/', '.')[:80]}"
                if response.status_code < 400:
                    outcome = "success"
                elif response.status_code in (401, 403):
                    outcome = "denied"
                else:
                    outcome = "failed"
                details = json.dumps(
                    {
                        "path": request.url.path,
                        "status": response.status_code,
                        "outcome": outcome,
                        "request_id": getattr(request.state, "request_id", None),
                        "correlation_id": getattr(request.state, "correlation_id", None),
                        "causation_id": getattr(request.state, "causation_id", None),
                    }
                )
                
                def _do_insert():
                    try:
                        with _engine().connect() as conn:
                            conn.execute(
                                text(
                                    """
                                    INSERT INTO audit_logs (
                                      id, tenant_id, actor_id, actor_email, action,
                                      resource_type, resource_id, details, ip_address, timestamp
                                    ) VALUES (
                                      :id, :tenant_id, :actor_id, :actor_email, :action,
                                      'http_request', NULL, CAST(:details AS JSONB), :ip, NOW()
                                    )
                                    """
                                ),
                                {
                                    "id": str(uuid.uuid4()),
                                    "tenant_id": str(tid),
                                    "actor_id": str(aid) if aid else None,
                                    "actor_email": email,
                                    "action": action[:100],
                                    "details": details,
                                    "ip": request.client.host if request.client else None,
                                },
                            )
                            conn.commit()
                    except Exception as e:
                        logger.warning("audit_db_error", error=str(e))
                
                # Offload sync DB call to thread
                await anyio.to_thread.run_sync(_do_insert)

            except Exception as exc:
                logger.warning("audit_log.insert_failed", error=str(exc))

        return response
