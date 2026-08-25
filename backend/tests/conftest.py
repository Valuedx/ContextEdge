from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from fastapi import HTTPException

SRC_DIR = Path(__file__).resolve().parents[1] / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


def make_user(roles: list[str] | None = None, tenant_id=None, principal_type: str = "user"):
    assigned_roles = roles or []

    def has_exact_role(role: str) -> bool:
        return role in assigned_roles

    def has_role(role: str) -> bool:
        if "platform_super_admin" in assigned_roles:
            return True
        if role == "platform_super_admin":
            return False
        if "tenant_admin" in assigned_roles or "admin" in assigned_roles:
            return True
        return role in assigned_roles

    def require_role(role: str):
        if not has_role(role):
            raise HTTPException(status_code=403, detail=f"Role '{role}' required")

    def require_exact_role(role: str):
        if not has_exact_role(role):
            raise HTTPException(status_code=403, detail=f"Role '{role}' required")

    def require_any_role(*needed: str):
        if not any(has_role(role) for role in needed):
            raise HTTPException(status_code=403, detail="forbidden")

    return SimpleNamespace(
        user_id=uuid4(),
        tenant_id=tenant_id or uuid4(),
        email="test@example.com",
        roles=assigned_roles,
        workspace_ids=[],
        principal_type=principal_type,
        allowed_domain_ids=None,
        has_exact_role=has_exact_role,
        has_role=has_role,
        require_role=require_role,
        require_exact_role=require_exact_role,
        require_any_role=require_any_role,
    )
