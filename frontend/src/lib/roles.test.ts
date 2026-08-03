import { describe, expect, it } from "vitest";

import {
  canEditAutomationMode,
  canTransitionPlaybook,
} from "./roles";

describe("canEditAutomationMode", () => {
  it("is narrower than editing the playbook", () => {
    // Automation mode decides whether a playbook may act on a real
    // system: `suggest_only` caps every caller at read_only regardless
    // of their own role, so raising it is what makes every other
    // approval gate load-bearing. Authoring a procedure and authorising
    // it to take destructive action are different privileges.
    expect(canEditAutomationMode(["tenant_admin"])).toBe(true);
    expect(canEditAutomationMode(["knowledge_manager"])).toBe(false);
    expect(canEditAutomationMode(["playbook_reviewer"])).toBe(false);
    expect(canEditAutomationMode(["domain_admin"])).toBe(false);
    expect(canEditAutomationMode([])).toBe(false);
  });

  it("still admits the platform super-role", () => {
    expect(canEditAutomationMode(["platform_super_admin"])).toBe(true);
  });

  it("is strictly narrower than transitioning a playbook", () => {
    // A reviewer may move a playbook through its lifecycle without
    // being able to authorise it to act.
    for (const role of ["knowledge_manager", "playbook_reviewer"]) {
      expect(canTransitionPlaybook([role])).toBe(true);
      expect(canEditAutomationMode([role])).toBe(false);
    }
  });
});
