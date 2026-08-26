from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Request, status
from jose import jwt
from passlib.context import CryptContext
from sqlalchemy import select

from contextedge.config import settings
from contextedge.database import async_session_factory
from contextedge.deps import DbSession
from contextedge.models.tenant import RoleBinding, Tenant, User
from contextedge.schemas.tenant import LoginRequest, TokenResponse
from contextedge.services.copilot_audit_service import record_login_event
from contextedge.tenant_rls import bind_session_tenant

router = APIRouter()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Hash of an unguessable throwaway value, verified on the no-candidate path
# so "username exists" and "username doesn't exist" take the same time.
_DUMMY_PASSWORD_HASH = pwd_context.hash("contextedge-timing-equalizer")


def _create_token(user: User, roles: list[str], workspace_ids: list) -> str:
    expire = datetime.now(UTC) + timedelta(
        minutes=settings.jwt_access_token_expire_minutes
    )
    payload = {
        "sub": str(user.id),
        "tenant_id": str(user.tenant_id),
        "username": user.username,
        "email": user.username,
        "roles": roles,
        "workspace_ids": [str(w) for w in workspace_ids],
        "exp": expire,
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def _client_name(request: Request) -> str:
    raw = (request.headers.get("x-client") or "").strip().lower()
    if raw in {"extension", "dashboard"}:
        return raw
    return "extension" if raw else "dashboard"


def _login_meta(request: Request) -> dict[str, str | None]:
    forwarded = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    ip_address = forwarded or (request.client.host if request.client else None)
    return {
        "client": _client_name(request),
        "extension_version": (request.headers.get("x-extension-version") or "").strip() or None,
        "ip_address": ip_address,
        "user_agent": (request.headers.get("user-agent") or "").strip() or None,
    }


async def _persist_login_event(**kwargs) -> None:
    try:
        async with async_session_factory() as session:
            await bind_session_tenant(session, kwargs.get("tenant_id"), bypass=True)
            await record_login_event(session, **kwargs)
            await session.commit()
    except Exception:
        import structlog

        structlog.get_logger().warning("copilot.login_event_failed", exc_info=True)


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, db: DbSession, request: Request):
    import anyio

    meta = _login_meta(request)
    await bind_session_tenant(db, None, bypass=True)
    stmt = select(User).where(User.username == body.username, User.status == "active")
    if body.tenant_slug:
        stmt = stmt.join(Tenant, Tenant.id == User.tenant_id).where(
            Tenant.slug == body.tenant_slug
        )
    result = await db.execute(stmt.order_by(User.created_at.asc()).limit(5))
    candidates = [u for u in result.scalars().all() if u.password_hash]
    if len(candidates) == 5:
        import structlog

        structlog.get_logger().warning(
            "auth.candidate_cap_reached", username=body.username
        )
    if not candidates:
        await anyio.to_thread.run_sync(
            pwd_context.verify, body.password, _DUMMY_PASSWORD_HASH
        )
        tenant_id = None
        if body.tenant_slug:
            tenant = (
                await db.execute(select(Tenant).where(Tenant.slug == body.tenant_slug))
            ).scalar_one_or_none()
            tenant_id = tenant.id if tenant else None
        await _persist_login_event(
            tenant_id=tenant_id,
            user_id=None,
            username=body.username,
            success=False,
            failure_reason="invalid_credentials",
            **meta,
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    matching = []
    for candidate in candidates:
        verified = await anyio.to_thread.run_sync(
            pwd_context.verify, body.password, candidate.password_hash
        )
        if verified:
            matching.append(candidate)
    if not matching:
        await _persist_login_event(
            tenant_id=candidates[0].tenant_id,
            user_id=candidates[0].id if len(candidates) == 1 else None,
            username=body.username,
            success=False,
            failure_reason="invalid_credentials",
            **meta,
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    if len(matching) > 1:
        import structlog

        structlog.get_logger().warning(
            "auth.ambiguous_login_rejected",
            username=body.username,
            tenant_ids=[str(u.tenant_id) for u in matching],
        )
        await _persist_login_event(
            tenant_id=matching[0].tenant_id,
            user_id=None,
            username=body.username,
            success=False,
            failure_reason="ambiguous_account",
            **meta,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Ambiguous account; contact your administrator",
        )
    user = matching[0]

    rb_result = await db.execute(select(RoleBinding).where(RoleBinding.user_id == user.id))
    bindings = list(rb_result.scalars().all())
    roles = [b.role for b in bindings]
    workspace_ids = [
        b.scope_id for b in bindings if b.scope_type == "workspace" and b.scope_id is not None
    ]

    token = _create_token(user, roles, workspace_ids)
    await _persist_login_event(
        tenant_id=user.tenant_id,
        user_id=user.id,
        username=user.username,
        success=True,
        failure_reason=None,
        **meta,
    )
    return TokenResponse(
        access_token=token,
        expires_in=settings.jwt_access_token_expire_minutes * 60,
    )
