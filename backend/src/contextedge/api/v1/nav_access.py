from __future__ import annotations

from collections import defaultdict
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from contextedge.deps import AuthUser, DbSession
from contextedge.models.tenant import RoleNavAccess

router = APIRouter()

NAV_TABS: tuple[tuple[str, str], ...] = (
    ("Overview", "/overview"),
    ("Sources", "/sources"),
    ("Sync Operations", "/sync"),
    ("Evidence", "/evidence"),
    ("Sessions", "/sessions"),
    ("Runtime", "/runtime"),
    ("Reviewer Console", "/review"),
    ("Execution", "/execution"),
    ("Decisions", "/decisions"),
    ("Episodes", "/episodes"),
    ("Patterns", "/patterns"),
    ("Playbooks", "/playbooks"),
    ("Neg. Knowledge", "/negative-knowledge"),
    ("Identities", "/identities"),
    ("Correlations", "/correlations"),
    ("Suggestions", "/suggestions"),
    ("Graph Explorer", "/graph-explorer"),
    ("Contradictions", "/contradictions"),
    ("Drift", "/drift"),
    ("Evaluations", "/evaluations"),
    ("Policies", "/policies"),
    ("Audit Log", "/audit"),
    ("LLM Cost", "/admin/cost"),
    ("Pipeline Health", "/admin/pipeline"),
    ("Settings", "/settings"),
)

ALL_HREFS = {href for _, href in NAV_TABS}
EDITABLE_ROLES = (
    "analyst",
    "playbook_reviewer",
    "knowledge_manager",
    "domain_admin",
    "tenant_admin",
)

# Starting access before a super admin customizes it in Settings → Tab access.
DEFAULT_ACCESS: dict[str, tuple[str, ...]] = {
    "analyst": (
        "/overview",
        "/evidence",
        "/sessions",
        "/runtime",
        "/decisions",
        "/episodes",
        "/patterns",
        "/playbooks",
        "/graph-explorer",
    ),
    "playbook_reviewer": (
        "/overview",
        "/review",
        "/episodes",
        "/patterns",
        "/playbooks",
        "/graph-explorer",
    ),
    "knowledge_manager": (
        "/overview",
        "/evidence",
        "/sessions",
        "/runtime",
        "/review",
        "/execution",
        "/decisions",
        "/episodes",
        "/patterns",
        "/playbooks",
        "/negative-knowledge",
        "/identities",
        "/correlations",
        "/suggestions",
        "/graph-explorer",
        "/contradictions",
        "/drift",
        "/evaluations",
    ),
    "domain_admin": (
        "/overview",
        "/sources",
        "/sync",
        "/evidence",
        "/sessions",
        "/execution",
        "/decisions",
        "/identities",
        "/graph-explorer",
        "/audit",
    ),
    "tenant_admin": tuple(href for _, href in NAV_TABS),
}


class NavTabOut(BaseModel):
    label: str
    href: str


class NavAccessResponse(BaseModel):
    tabs: list[NavTabOut]
    roles: list[str]
    access: dict[str, list[str]]


class NavAccessUpdate(BaseModel):
    access: dict[str, list[str]] = Field(default_factory=dict)


def _defaults() -> dict[str, list[str]]:
    return {role: list(hrefs) for role, hrefs in DEFAULT_ACCESS.items()}


async def load_access_map(db: DbSession) -> dict[str, list[str]]:
    rows = (await db.execute(select(RoleNavAccess))).scalars().all()
    if not rows:
        return _defaults()
    grouped: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        if row.href in ALL_HREFS:
            grouped[row.role].append(row.href)
    access = _defaults()
    for role in EDITABLE_ROLES:
        if role in grouped:
            access[role] = grouped[role]
    return access


@router.get("", response_model=NavAccessResponse)
async def get_nav_access(db: DbSession, _user: AuthUser):
    access = await load_access_map(db)
    return NavAccessResponse(
        tabs=[NavTabOut(label=label, href=href) for label, href in NAV_TABS],
        roles=list(EDITABLE_ROLES),
        access=access,
    )


@router.put("", response_model=NavAccessResponse)
async def put_nav_access(body: NavAccessUpdate, db: DbSession, user: AuthUser):
    if not user.has_exact_role("platform_super_admin"):
        raise HTTPException(status_code=403, detail="Only the platform super admin can change tab access")
    next_access: dict[str, list[str]] = {}
    for role in EDITABLE_ROLES:
        hrefs = body.access.get(role)
        if hrefs is None:
            current = await load_access_map(db)
            hrefs = current.get(role, list(DEFAULT_ACCESS[role]))
        cleaned: list[str] = []
        seen: set[str] = set()
        for href in hrefs:
            if href not in ALL_HREFS or href in seen:
                continue
            seen.add(href)
            cleaned.append(href)
        if "/overview" not in cleaned:
            cleaned.insert(0, "/overview")
        next_access[role] = cleaned

    await db.execute(delete(RoleNavAccess))
    for role, hrefs in next_access.items():
        for href in hrefs:
            db.add(RoleNavAccess(id=uuid4(), role=role, href=href))
    await db.flush()
    return NavAccessResponse(
        tabs=[NavTabOut(label=label, href=href) for label, href in NAV_TABS],
        roles=list(EDITABLE_ROLES),
        access=next_access,
    )
