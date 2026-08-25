/**
 * Sidebar tab catalog. Role-to-tab mapping is stored in the database and
 * edited by the platform super admin in Settings → Tab access.
 */

import { canSeeNav } from "@/lib/roles";

export const A = "analyst";
export const PR = "playbook_reviewer";
export const KM = "knowledge_manager";
export const DA = "domain_admin";
export const TA = "tenant_admin";
export const SA = "platform_super_admin";

export interface NavAccessItem {
  label: string;
  href: string;
  requiredRoles: string[];
}

export type RoleTabAccess = Record<string, string[]>;

export const NAV_ITEMS: NavAccessItem[] = [
  { label: "Overview", href: "/overview", requiredRoles: [A, PR, KM, DA, TA, SA] },
  { label: "Sources", href: "/sources", requiredRoles: [DA, TA, SA] },
  { label: "Sync Operations", href: "/sync", requiredRoles: [DA, TA, SA] },
  { label: "Evidence", href: "/evidence", requiredRoles: [A, KM, DA, TA, SA] },
  { label: "Sessions", href: "/sessions", requiredRoles: [A, KM, DA, TA, SA] },
  { label: "Runtime", href: "/runtime", requiredRoles: [A, KM, TA, SA] },
  { label: "Reviewer Console", href: "/review", requiredRoles: [PR, KM, TA, SA] },
  { label: "Execution", href: "/execution", requiredRoles: [DA, KM, TA, SA] },
  { label: "Decisions", href: "/decisions", requiredRoles: [A, KM, DA, TA, SA] },
  { label: "Episodes", href: "/episodes", requiredRoles: [A, PR, KM, TA, SA] },
  { label: "Patterns", href: "/patterns", requiredRoles: [A, PR, KM, TA, SA] },
  { label: "Playbooks", href: "/playbooks", requiredRoles: [A, PR, KM, TA, SA] },
  { label: "Neg. Knowledge", href: "/negative-knowledge", requiredRoles: [KM, TA, SA] },
  { label: "Identities", href: "/identities", requiredRoles: [KM, DA, TA, SA] },
  { label: "Correlations", href: "/correlations", requiredRoles: [KM, TA, SA] },
  { label: "Suggestions", href: "/suggestions", requiredRoles: [KM, TA, SA] },
  { label: "Graph Explorer", href: "/graph-explorer", requiredRoles: [A, PR, KM, DA, TA, SA] },
  { label: "Contradictions", href: "/contradictions", requiredRoles: [KM, TA, SA] },
  { label: "Drift", href: "/drift", requiredRoles: [KM, TA, SA] },
  { label: "Evaluations", href: "/evaluations", requiredRoles: [KM, TA, SA] },
  { label: "Policies", href: "/policies", requiredRoles: [TA, SA] },
  { label: "Audit Log", href: "/audit", requiredRoles: [TA, SA, DA] },
  { label: "LLM Cost", href: "/admin/cost", requiredRoles: [TA, SA] },
  { label: "Pipeline Health", href: "/admin/pipeline", requiredRoles: [TA, SA] },
  { label: "Settings", href: "/settings", requiredRoles: [TA, SA] },
];

export function seesAllSidebarTabs(userRoles: string[]): boolean {
  return userRoles.includes("platform_super_admin");
}

export function canSeeSidebarItem(
  userRoles: string[],
  item: NavAccessItem,
  access?: RoleTabAccess | null,
): boolean {
  if (seesAllSidebarTabs(userRoles)) return true;
  if (access) {
    const hrefs = new Set<string>();
    for (const role of userRoles) {
      for (const href of access[role] ?? []) hrefs.add(href);
    }
    if (userRoles.includes("admin")) {
      for (const href of access.tenant_admin ?? []) hrefs.add(href);
    }
    return hrefs.has(item.href);
  }
  if (userRoles.includes("tenant_admin") || userRoles.includes("admin")) return true;
  return canSeeNav(userRoles, item.requiredRoles);
}

export function sidebarTabsForRoles(
  userRoles: string[],
  access?: RoleTabAccess | null,
): NavAccessItem[] {
  return NAV_ITEMS.filter((item) => canSeeSidebarItem(userRoles, item, access));
}

export function tabsGrantedByRole(role: string, access?: RoleTabAccess | null): string[] {
  if (role === "platform_super_admin") {
    return NAV_ITEMS.map((item) => item.label);
  }
  if (access?.[role]) {
    const allowed = new Set(access[role]);
    return NAV_ITEMS.filter((item) => allowed.has(item.href)).map((item) => item.label);
  }
  if (role === "tenant_admin" || role === "admin") {
    return NAV_ITEMS.map((item) => item.label);
  }
  return NAV_ITEMS.filter((item) => item.requiredRoles.includes(role)).map((item) => item.label);
}

export interface NavAccessPayload {
  tabs: { label: string; href: string }[];
  roles: string[];
  access: RoleTabAccess;
}
