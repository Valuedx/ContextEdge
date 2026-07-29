from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, status
from jose import jwt
from passlib.context import CryptContext
from sqlalchemy import select

from contextedge.config import settings
from contextedge.deps import DbSession
from contextedge.models.tenant import User, RoleBinding
from contextedge.schemas.tenant import LoginRequest, TokenResponse

router = APIRouter()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def _create_token(user: User, roles: list[str]) -> str:
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=settings.jwt_access_token_expire_minutes
    )
    payload = {
        "sub": str(user.id),
        "tenant_id": str(user.tenant_id),
        "email": user.email,
        "roles": roles,
        "exp": expire,
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, db: DbSession):
    # User.email is not globally unique (two tenants can hold the same
    # address), so fetch all matches instead of scalar_one_or_none() — which
    # raises MultipleResultsFound and turns a duplicate email into a 500.
    result = await db.execute(
        select(User).where(User.email == body.email, User.status == "active")
    )
    candidates = [u for u in result.scalars().all() if u.password_hash]
    matching = [
        u for u in candidates if pwd_context.verify(body.password, u.password_hash)
    ]
    if not matching:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    if len(matching) > 1:
        # Same email + same password across tenants: refusing is safer than
        # guessing which tenant the caller meant.
        import structlog

        structlog.get_logger().warning(
            "auth.ambiguous_login_rejected",
            email=body.email,
            tenant_ids=[str(u.tenant_id) for u in matching],
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Ambiguous account; contact your administrator",
        )
    user = matching[0]

    rb_result = await db.execute(
        select(RoleBinding.role).where(RoleBinding.user_id == user.id)
    )
    roles = [r for (r,) in rb_result.all()]

    token = _create_token(user, roles)
    return TokenResponse(
        access_token=token,
        expires_in=settings.jwt_access_token_expire_minutes * 60,
    )
