from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.config import settings
from contextedge.database import get_db
from contextedge.models.tenant import RoleBinding, Tenant, User
from contextedge.security_tokens import service_token_context
from contextedge.tenant_rls import bind_session_tenant

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

    def has_exact_role(self, role: str) -> bool:
        return role in self.roles

    def has_role(self, role: str) -> bool:
        if "platform_super_admin" in self.roles:
            return True
        if role == "platform_super_admin":
            return False
        if "tenant_admin" in self.roles or "admin" in self.roles:
            return True
        return role in self.roles

    def can_assign_role(self, role: str) -> bool:
        if role == "platform_super_admin":
            return self.has_exact_role("platform_super_admin")
        return self.has_role("tenant_admin")

    def require_role(self, role: str) -> None:
        if not self.has_role(role):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{role}' required",
            )

    def require_exact_role(self, role: str) -> None:
        if not self.has_exact_role(role):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{role}' required",
            )

    def require_any_role(self, *roles: str) -> None:
        if not any(self.has_role(role) for role in roles):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"One of roles {list(roles)} required",
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
    db: Annotated[AsyncSession, Depends(get_db)],
    x_service_token: str | None = Header(default=None, alias="X-Service-Token"),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
) -> CurrentUser:
    if x_service_token:
        principal = _service_principal(x_service_token.strip())
        if principal:
            await bind_session_tenant(db, principal.tenant_id, bypass=False)
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
        user_id = UUID(payload["sub"])
        token_tenant_id = UUID(payload["tenant_id"])
        await bind_session_tenant(db, token_tenant_id, bypass=False)
        account = (
            await db.execute(select(User).where(User.id == user_id))
        ).scalar_one_or_none()
        if account is None or account.status != "active":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired token",
            )
        if account.tenant_id != token_tenant_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired token",
            )
        bindings = (
            await db.execute(select(RoleBinding).where(RoleBinding.user_id == account.id))
        ).scalars().all()
        roles = [b.role for b in bindings]
        workspace_ids = [
            b.scope_id
            for b in bindings
            if b.scope_type == "workspace" and b.scope_id is not None
        ]
        allowed_from_bindings = [
            b.scope_id
            for b in bindings
            if b.scope_type == "domain" and b.scope_id is not None
        ]
        principal = CurrentUser(
            user_id=account.id,
            tenant_id=account.tenant_id,
            email=account.username,
            roles=roles,
            workspace_ids=workspace_ids,
            principal_type="user",
            allowed_domain_ids=allowed_from_bindings or None,
        )
        if x_tenant_id and principal.has_exact_role("platform_super_admin"):
            try:
                override = UUID(x_tenant_id.strip())
            except ValueError as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid tenant",
                ) from exc
            tenant = (
                await db.execute(select(Tenant).where(Tenant.id == override, Tenant.is_active.is_(True)))
            ).scalar_one_or_none()
            if tenant is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
            principal.tenant_id = tenant.id
            await bind_session_tenant(db, principal.tenant_id, bypass=False)
        return principal
    except HTTPException:
        raise
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
