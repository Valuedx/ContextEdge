from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Request, status
from jose import jwt
from passlib.context import CryptContext
from sqlalchemy import select

from contextedge.config import settings
from contextedge.deps import DbSession
from contextedge.models.tenant import RoleBinding, Tenant, User
from contextedge.schemas.tenant import LoginRequest, TokenResponse
from contextedge.tenant_rls import bind_session_scope

router = APIRouter()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Hash of an unguessable throwaway value, verified on the no-candidate path
# so "username exists" and "username doesn't exist" take the same time.
_DUMMY_PASSWORD_HASH = pwd_context.hash("contextedge-timing-equalizer")


def _create_token(
    user: User,
    roles: list[str],
    workspace_ids: list,
    expire_minutes: int | None = None,
) -> str:
    minutes = expire_minutes if expire_minutes is not None else settings.jwt_access_token_expire_minutes
    expire = datetime.now(UTC) + timedelta(minutes=minutes)
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


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, db: DbSession, request: Request):
    import anyio

    # Platform scope: login resolves a user before it knows which client
    # they belong to, so it cannot be client-scoped.
    await bind_session_scope(db, msp_id=None, tenant_id=None)
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
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    matching = []
    for candidate in candidates:
        verified = await anyio.to_thread.run_sync(
            pwd_context.verify, body.password, candidate.password_hash
        )
        if verified:
            matching.append(candidate)
    if not matching:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    if len(matching) > 1:
        import structlog

        structlog.get_logger().warning(
            "auth.ambiguous_login_rejected",
            username=body.username,
            tenant_ids=[str(u.tenant_id) for u in matching],
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

    client_name = _client_name(request)
    expire_minutes = (
        max(settings.jwt_access_token_expire_minutes, 525600)
        if client_name == "extension"
        else settings.jwt_access_token_expire_minutes
    )
    token = _create_token(user, roles, workspace_ids, expire_minutes=expire_minutes)
    return TokenResponse(
        access_token=token,
        expires_in=expire_minutes * 60,
    )
