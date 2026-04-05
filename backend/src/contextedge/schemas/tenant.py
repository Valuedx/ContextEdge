from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


class TenantCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    slug: str = Field(..., min_length=1, max_length=100, pattern=r"^[a-z0-9-]+$")
    config: dict = Field(default_factory=dict)
    retention_defaults: dict | None = None


class TenantUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    config: dict | None = None
    retention_defaults: dict | None = None
    is_active: bool | None = None


class TenantResponse(BaseModel):
    id: UUID
    name: str
    slug: str
    config: dict
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class WorkspaceCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    config: dict = Field(default_factory=dict)


class WorkspaceUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = None
    config: dict | None = None
    is_active: bool | None = None


class WorkspaceResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    name: str
    description: str | None
    config: dict
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class DomainCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    workspace_id: UUID | None = None


class DomainUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = None
    is_active: bool | None = None


class DomainResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    workspace_id: UUID | None
    name: str
    description: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class UserCreate(BaseModel):
    email: EmailStr
    display_name: str = Field(..., min_length=1, max_length=255)
    password: str | None = Field(None, min_length=8)
    external_id: str | None = None
    sso_provider: str | None = None


class UserUpdate(BaseModel):
    display_name: str | None = Field(None, min_length=1, max_length=255)
    status: str | None = None
    password: str | None = Field(None, min_length=8)


class UserResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    email: str
    display_name: str
    status: str
    sso_provider: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class RoleBindingCreate(BaseModel):
    user_id: UUID
    role: str = Field(..., pattern=r"^[a-z_]+$")
    scope_type: str = "tenant"
    scope_id: UUID | None = None


class RoleBindingResponse(BaseModel):
    id: UUID
    user_id: UUID
    tenant_id: UUID
    role: str
    scope_type: str
    scope_id: UUID | None
    created_at: datetime

    model_config = {"from_attributes": True}


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
