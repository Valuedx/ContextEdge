/**
 * Role helper utilities — single source of truth for permission checks.
 *
 * All helpers treat `platform_super_admin` as a super-role that satisfies any check.
 */

export function hasRole(roles: string[], role: string): boolean {
  return roles.includes(role) || roles.includes("platform_super_admin");
}

// ── Named predicates ─────────────────────────────────────────────────────────

export const isTenantAdmin = (roles: string[]) => hasRole(roles, "tenant_admin");
export const isDomainAdmin = (roles: string[]) => hasRole(roles, "domain_admin");
export const isKnowledgeManager = (roles: string[]) => hasRole(roles, "knowledge_manager");
export const isPlaybookReviewer = (roles: string[]) => hasRole(roles, "playbook_reviewer");
export const isAnalyst = (roles: string[]) => hasRole(roles, "analyst");

// ── Composite capability checks ───────────────────────────────────────────────

/** Can approve/reject episodes */
export const canApproveEpisode = (roles: string[]) => isKnowledgeManager(roles);

/** Can manage evaluations (create/run/delete) */
export const canManageEval = (roles: string[]) => isKnowledgeManager(roles);

/** Can transition playbook status */
export const canTransitionPlaybook = (roles: string[]) =>
  isPlaybookReviewer(roles) || isKnowledgeManager(roles) || isTenantAdmin(roles);

/** Can trigger source discovery */
export const canDiscoverSources = (roles: string[]) => isDomainAdmin(roles);

/** Can list/manage policies attached to a source */
export const canListPoliciesForSource = (roles: string[]) =>
  isTenantAdmin(roles) || isDomainAdmin(roles);

/** Can list policies attached to an evidence item */
export const canListPoliciesForEvidence = (roles: string[]) =>
  isTenantAdmin(roles) || isDomainAdmin(roles) || isKnowledgeManager(roles);

/** Can edit the access policy of an evidence item */
export const canEditEvidenceAccessPolicy = (roles: string[]) =>
  isDomainAdmin(roles) || isKnowledgeManager(roles);
