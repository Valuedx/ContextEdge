from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.config import settings
from contextedge.database import get_db
from contextedge.security_tokens import service_token_context

security = HTTPBearer(auto_error=False)


class CurrentUser:
    """Represents the authenticated principal (human JWT or service token)."""

    def __init__(
        self,
        user_id: UUID,
        tenant_id: UUID,
        email: str,
        roles: list[str],
        workspace_ids: list[UUID] | None = None,
        principal_type: str = "user",
        allowed_domain_ids: list[UUID] | None = None,
    ):
        self.user_id = user_id
        self.tenant_id = tenant_id
        self.email = email
        self.roles = roles
        self.workspace_ids = workspace_ids
        self.principal_type = principal_type
        self.allowed_domain_ids = allowed_domain_ids

    def has_role(self, role: str) -> bool:
        if (
            "platform_super_admin" in self.roles
            or "tenant_admin" in self.roles
            or "admin" in self.roles
        ):
            return True
        return role in self.roles

    def require_role(self, role: str) -> None:
        if not self.has_role(role):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{role}' required",
            )


def _service_principal(token: str) -> CurrentUser | None:
    ctx = service_token_context(token)
    if not ctx:
        return None
    allowed: list[UUID] | None = None
    if "allowed_domain_ids" in ctx:
        allowed = [UUID(x) for x in ctx["allowed_domain_ids"]]
    return CurrentUser(
        user_id=UUID(ctx["user_id"]),
        tenant_id=UUID(ctx["tenant_id"]),
        email=ctx["email"],
        roles=ctx["roles"],
        workspace_ids=[],
        principal_type="service_account",
        allowed_domain_ids=allowed,
    )


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)],
    x_service_token: str | None = Header(default=None, alias="X-Service-Token"),
) -> CurrentUser:
    if x_service_token:
        principal = _service_principal(x_service_token.strip())
        if principal:
            return principal
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid service token",
        )

    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )

    try:
        payload = jwt.decode(
            credentials.credentials,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )
        jwt_domains = payload.get("allowed_domain_ids")
        allowed_jwt: list[UUID] | None = None
        if isinstance(jwt_domains, list):
            allowed_jwt = [UUID(str(x)) for x in jwt_domains]
        return CurrentUser(
            user_id=UUID(payload["sub"]),
            tenant_id=UUID(payload["tenant_id"]),
            email=payload.get("email", ""),
            roles=payload.get("roles", []),
            workspace_ids=[UUID(w) for w in payload.get("workspace_ids", [])],
            principal_type="user",
            allowed_domain_ids=allowed_jwt,
        )
    except (JWTError, KeyError, ValueError) as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        ) from e


def require_role(role: str):
    """Dependency factory that checks the user has a specific role."""

    async def _check(user: Annotated[CurrentUser, Depends(get_current_user)]) -> CurrentUser:
        user.require_role(role)
        return user

    return _check


DbSession = Annotated[AsyncSession, Depends(get_db)]
AuthUser = Annotated[CurrentUser, Depends(get_current_user)]
