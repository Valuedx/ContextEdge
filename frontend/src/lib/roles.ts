/**
 * Role helper utilities — single source of truth for permission checks.
 *
 * Keep this hierarchy aligned with CurrentUser.has_role in the API.
 */

export const ROLE_LABELS: Record<string, string> = {
  platform_super_admin: "Platform super admin",
  tenant_admin: "Tenant administrator",
  domain_admin: "Domain administrator",
  knowledge_manager: "Knowledge manager",
  playbook_reviewer: "Playbook reviewer",
  analyst: "Analyst",
};

export function roleLabel(role: string): string {
  return ROLE_LABELS[role] ?? role.replaceAll("_", " ");
}

export const TENANT_ASSIGNABLE_ROLES = [
  { value: "analyst", label: ROLE_LABELS.analyst },
  { value: "knowledge_manager", label: ROLE_LABELS.knowledge_manager },
  { value: "playbook_reviewer", label: ROLE_LABELS.playbook_reviewer },
  { value: "domain_admin", label: ROLE_LABELS.domain_admin },
  { value: "tenant_admin", label: ROLE_LABELS.tenant_admin },
] as const;

export function assignableRoles(actorRoles: string[]) {
  if (actorRoles.includes("platform_super_admin")) {
    return [
      { value: "platform_super_admin", label: ROLE_LABELS.platform_super_admin },
      ...TENANT_ASSIGNABLE_ROLES,
    ];
  }
  return [...TENANT_ASSIGNABLE_ROLES];
}

export function hasRole(roles: string[], role: string): boolean {
  return (
    roles.includes(role) ||
    roles.includes("platform_super_admin") ||
    roles.includes("tenant_admin") ||
    roles.includes("admin")
  );
}

export const isPlatformSuperAdmin = (roles: string[]) =>
  roles.includes("platform_super_admin");

function navRoles(roles: string[]): string[] {
  const next = new Set(roles.filter(Boolean));
  if (next.has("admin")) next.add("tenant_admin");
  return [...next];
}

/** Sidebar and settings tabs: exact role match unless a helper grants all tabs. */
export function canSeeNav(userRoles: string[], allowed: readonly string[]): boolean {
  const have = navRoles(userRoles);
  return allowed.some((role) => have.includes(role));
}

export const SETTINGS_TABS = {
  general: ["platform_super_admin", "tenant_admin"],
  tenants: ["platform_super_admin"],
  workspaces: ["tenant_admin", "platform_super_admin"],
  domains: ["tenant_admin", "platform_super_admin"],
  users: ["tenant_admin", "platform_super_admin"],
  retention: ["tenant_admin", "platform_super_admin"],
  tabAccess: ["platform_super_admin"],
} as const;

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
  isPlaybookReviewer(roles);

/**
 * Can change a playbook's automation mode.
 *
 * Deliberately narrower than editing a playbook. Automation mode decides
 * whether a playbook may act on a real system at all — `suggest_only`
 * caps every caller at read_only regardless of their own role, so
 * raising it is what makes every other approval gate load-bearing.
 * Editing a playbook's text and authorising it to take destructive
 * action are not the same privilege.
 */
export const canEditAutomationMode = (roles: string[]) => isTenantAdmin(roles);

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
