from fastapi import APIRouter

router = APIRouter()

from contextedge.api.v1 import (  # noqa: E402, F401
    auth, tenants, workspaces, domains, users, audit,
    sources, sync, evidence, threads, episodes,
    patterns, playbooks, runtime, evaluations, policies, drift,
)

router.include_router(auth.router, prefix="/auth", tags=["auth"])
router.include_router(tenants.router, prefix="/tenants", tags=["tenants"])
router.include_router(workspaces.router, prefix="/workspaces", tags=["workspaces"])
router.include_router(domains.router, prefix="/domains", tags=["domains"])
router.include_router(users.router, prefix="/users", tags=["users"])
router.include_router(audit.router, prefix="/audit-logs", tags=["audit"])
router.include_router(sources.router, prefix="/sources", tags=["sources"])
router.include_router(sync.router, prefix="/sync-runs", tags=["sync"])
router.include_router(evidence.router, prefix="/evidence", tags=["evidence"])
router.include_router(threads.router, prefix="/threads", tags=["threads"])
router.include_router(episodes.router, prefix="/episodes", tags=["episodes"])
router.include_router(patterns.router, prefix="/patterns", tags=["patterns"])
router.include_router(playbooks.router, prefix="/playbooks", tags=["playbooks"])
router.include_router(runtime.router, prefix="/runtime", tags=["runtime"])
router.include_router(evaluations.router, prefix="/evaluations", tags=["evaluations"])
router.include_router(policies.router, prefix="/policies", tags=["policies"])
router.include_router(drift.router, prefix="/drift", tags=["drift"])
