"""Shared parsing for `X-Service-Token` / `service_tokens_json` configuration."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from contextedge.config import settings


def service_token_context(token: str) -> dict[str, Any] | None:
    raw = (settings.service_tokens_json or "").strip()
    if not raw or raw == "{}":
        return None
    try:
        registry = json.loads(raw)
    except json.JSONDecodeError:
        return None
    meta = registry.get(token)
    if not meta:
        return None
    UUID(meta["tenant_id"])
    UUID(meta["user_id"])
    out: dict[str, Any] = {
        "tenant_id": str(meta["tenant_id"]),
        "user_id": str(meta["user_id"]),
        "email": meta.get("email", "service@contextedge.local"),
        "roles": list(meta.get("roles", ["service_account"])),
    }
    if "allowed_domain_ids" in meta:
        parsed: list[str] = []
        for d in meta["allowed_domain_ids"] or []:
            parsed.append(str(UUID(str(d))))
        out["allowed_domain_ids"] = parsed
    return out
