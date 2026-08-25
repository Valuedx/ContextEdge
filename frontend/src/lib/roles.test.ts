import { describe, expect, it } from "vitest";

import {
  SETTINGS_TABS,
  canEditAutomationMode,
  canSeeNav,
  canTransitionPlaybook,
  hasRole,
} from "./roles";
import { canSeeSidebarItem, canAccessDashboardPath, NAV_ITEMS } from "./nav";

describe("hasRole", () => {
  it("does not treat tenant admin as platform super admin", () => {
    expect(hasRole(["tenant_admin"], "platform_super_admin")).toBe(false);
    expect(hasRole(["tenant_admin"], "knowledge_manager")).toBe(true);
    expect(hasRole(["platform_super_admin"], "analyst")).toBe(true);
    expect(hasRole(["analyst"], "domain_admin")).toBe(false);
  });
});

describe("canEditAutomationMode", () => {
  it("is narrower than editing the playbook", () => {
    expect(canEditAutomationMode(["tenant_admin"])).toBe(true);
    expect(canEditAutomationMode(["knowledge_manager"])).toBe(false);
    expect(canEditAutomationMode(["playbook_reviewer"])).toBe(false);
    expect(canEditAutomationMode(["domain_admin"])).toBe(false);
    expect(canEditAutomationMode([])).toBe(false);
  });

  it("shows settings and tenant tabs only to the matching admin role", () => {
    expect(canSeeNav(["analyst"], SETTINGS_TABS.users)).toBe(false);
    expect(canSeeNav(["domain_admin"], SETTINGS_TABS.users)).toBe(false);
    expect(canSeeNav(["tenant_admin"], SETTINGS_TABS.users)).toBe(true);
    expect(canSeeNav(["tenant_admin"], SETTINGS_TABS.tenants)).toBe(false);
    expect(canSeeNav(["platform_super_admin"], SETTINGS_TABS.tenants)).toBe(true);
    expect(canSeeNav(["analyst"], ["domain_admin"])).toBe(false);
    expect(canSeeNav(["domain_admin"], ["domain_admin"])).toBe(true);
  });

  it("shows every sidebar tab to tenant admin until a custom map is loaded", () => {
    const sources = NAV_ITEMS.find((item) => item.href === "/sources");
    expect(sources).toBeTruthy();
    expect(canSeeSidebarItem(["tenant_admin"], sources!)).toBe(true);
    expect(canSeeSidebarItem(["analyst"], sources!)).toBe(false);
    expect(canSeeSidebarItem(["domain_admin"], sources!)).toBe(true);
    expect(NAV_ITEMS.every((item) => canSeeSidebarItem(["tenant_admin"], item))).toBe(true);
  });

  it("uses super-admin saved tab access when provided", () => {
    const sources = NAV_ITEMS.find((item) => item.href === "/sources")!;
    expect(
      canSeeSidebarItem(["analyst"], sources, { analyst: ["/overview", "/sources"] }),
    ).toBe(true);
    expect(canSeeSidebarItem(["analyst"], sources, { analyst: ["/overview"] })).toBe(false);
    expect(canSeeSidebarItem(["platform_super_admin"], sources, { analyst: ["/overview"] })).toBe(
      true,
    );
  });

  it("keeps playbook approval limited to reviewers and API admin super-roles", () => {
    expect(canTransitionPlaybook(["playbook_reviewer"])).toBe(true);
    expect(canTransitionPlaybook(["knowledge_manager"])).toBe(false);
    expect(canTransitionPlaybook(["tenant_admin"])).toBe(true);
    expect(canTransitionPlaybook(["admin"])).toBe(true);
    expect(canEditAutomationMode(["playbook_reviewer"])).toBe(false);
  });

  it("blocks analysts from admin dashboard routes", () => {
    expect(canAccessDashboardPath(["analyst"], "/sources")).toBe(false);
    expect(canAccessDashboardPath(["analyst"], "/settings")).toBe(false);
    expect(canAccessDashboardPath(["analyst"], "/overview")).toBe(true);
    expect(canAccessDashboardPath(["domain_admin"], "/sources/abc")).toBe(true);
    expect(canAccessDashboardPath(["tenant_admin"], "/settings")).toBe(true);
  });
});
