from fastapi import APIRouter

router = APIRouter()

from contextedge.api.v1 import (  # noqa: E402, F401
    action_policies,
    admin_cost,
    copilot,
    audit,
    auth,
    contradictions,
    correlations,
    decisions,
    domains,
    drift,
    episodes,
    evaluations,
    evidence,
    execution,
    graph,
    identities,
    inventory,
    knowledge_supersessions,
    negative_knowledge,
    notifications,
    patterns,
    playbooks,
    policies,
    policy_assignments,
    review_queue,
    runtime,
    sessions,
    skills,
    sources,
    sync,
    tenants,
    threads,
    users,
    nav_access,
    workspaces,
    agent,
    overview,
)

router.include_router(auth.router, prefix="/auth", tags=["auth"])
router.include_router(tenants.router, prefix="/tenants", tags=["tenants"])
router.include_router(workspaces.router, prefix="/workspaces", tags=["workspaces"])
router.include_router(domains.router, prefix="/domains", tags=["domains"])
router.include_router(users.router, prefix="/users", tags=["users"])
router.include_router(nav_access.router, prefix="/nav-access", tags=["nav-access"])
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
router.include_router(agent.router, prefix="/agent", tags=["agent"])
router.include_router(evaluations.router, prefix="/evaluations", tags=["evaluations"])
router.include_router(policies.router, prefix="/policies", tags=["policies"])
router.include_router(
    action_policies.router, prefix="/action-policies", tags=["action-policies"]
)
router.include_router(drift.router, prefix="/drift", tags=["drift"])
router.include_router(execution.router, prefix="/execution", tags=["execution"])
router.include_router(decisions.router, prefix="/decisions", tags=["decisions"])
router.include_router(contradictions.router, prefix="/contradictions", tags=["contradictions"])
router.include_router(notifications.router, prefix="/notifications", tags=["notifications"])
router.include_router(
    negative_knowledge.router, prefix="/negative-knowledge", tags=["negative-knowledge"]
)
router.include_router(identities.router, prefix="/identities", tags=["identities"])
router.include_router(correlations.router, prefix="/correlations", tags=["correlations"])
router.include_router(
    policy_assignments.router, prefix="/policy-assignments", tags=["policy-assignments"]
)
router.include_router(
    knowledge_supersessions.router,
    prefix="/knowledge-supersessions",
    tags=["knowledge-supersessions"],
)
router.include_router(skills.router, prefix="/skills", tags=["skills"])
router.include_router(graph.router, prefix="/graph", tags=["graph"])
router.include_router(inventory.router, prefix="/inventory", tags=["inventory"])
router.include_router(review_queue.router, prefix="/review-queue", tags=["review-queue"])
router.include_router(admin_cost.router, prefix="/admin", tags=["admin"])
router.include_router(copilot.router, prefix="/copilot", tags=["copilot"])
router.include_router(overview.router, prefix="/overview", tags=["overview"])
