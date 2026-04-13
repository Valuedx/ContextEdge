from fastapi import APIRouter

router = APIRouter()

from contextedge.api.v1 import (  # noqa: E402, F401
    auth, tenants, workspaces, domains, users, audit,
    sources, sync, evidence, threads, episodes,
    patterns, playbooks, runtime, evaluations, policies, drift, sessions,
    execution, contradictions, notifications, negative_knowledge,
    identities, correlations, policy_assignments,
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
router.include_router(sessions.router, prefix="/sessions", tags=["sessions"])
router.include_router(runtime.router, prefix="/runtime", tags=["runtime"])
router.include_router(evaluations.router, prefix="/evaluations", tags=["evaluations"])
router.include_router(policies.router, prefix="/policies", tags=["policies"])
router.include_router(drift.router, prefix="/drift", tags=["drift"])
router.include_router(execution.router, prefix="/execution", tags=["execution"])
router.include_router(contradictions.router, prefix="/contradictions", tags=["contradictions"])
router.include_router(notifications.router, prefix="/notifications", tags=["notifications"])
router.include_router(negative_knowledge.router, prefix="/negative-knowledge", tags=["negative-knowledge"])
router.include_router(identities.router, prefix="/identities", tags=["identities"])
router.include_router(correlations.router, prefix="/correlations", tags=["correlations"])
router.include_router(policy_assignments.router, prefix="/policy-assignments", tags=["policy-assignments"])
