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
    return SimpleNamespace(
        user_id=uuid4(),
        tenant_id=tenant_id or uuid4(),
        email="test@example.com",
        roles=assigned_roles,
        workspace_ids=[],
        principal_type=principal_type,
        allowed_domain_ids=None,
        has_role=lambda role: role in assigned_roles or "platform_super_admin" in assigned_roles,
        require_role=lambda role: (_ for _ in ()).throw(
            HTTPException(status_code=403, detail=f"Role '{role}' required")
        ) if role not in assigned_roles and "platform_super_admin" not in assigned_roles else None,
    )
