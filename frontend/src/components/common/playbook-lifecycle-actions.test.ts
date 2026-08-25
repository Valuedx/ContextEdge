import { describe, expect, it } from "vitest";

import type { Playbook } from "@/lib/types";
import {
  bulkTransitionTarget,
  lifecycleStateLabel,
  primaryTransition,
  transitionPayload,
} from "./playbook-lifecycle-actions";

function playbook(state: string, transitions: string[]): Playbook {
  return {
    id: "pb-1",
    tenant_id: "tenant-1",
    domain_id: null,
    stable_key: "pb-test",
    title: "Test",
    description: null,
    lifecycle_state: state,
    risk_tier: "medium",
    automation_mode: "suggest_only",
    allowed_transitions: transitions,
    owner_user_id: "user-1",
    reviewer_user_id: null,
    approver_user_id: null,
    current_version_id: null,
    last_validated_at: null,
    expiry_at: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  };
}

describe("playbook lifecycle actions", () => {
  it("uses the explicit review path for candidates", () => {
    expect(primaryTransition(playbook("candidate", ["under_review"]))).toBe("under_review");
  });

  it("makes approval the primary under-review action", () => {
    expect(primaryTransition(playbook("under_review", ["candidate", "approved"]))).toBe("approved");
  });

  it("sends the API field name for review comments", () => {
    expect(transitionPayload("approved", " Verified against ticket ")).toEqual({
      new_state: "approved",
      comments: "Verified against ticket",
    });
  });

  it("formats lifecycle labels for people", () => {
    expect(lifecycleStateLabel("under_review")).toBe("Under review");
  });

  it("advances a homogeneous candidate selection one review step", () => {
    expect(
      bulkTransitionTarget([
        playbook("candidate", ["under_review"]),
        { ...playbook("candidate", ["under_review"]), id: "pb-2" },
      ]),
    ).toBe("under_review");
  });

  it("approves a homogeneous under-review selection", () => {
    expect(
      bulkTransitionTarget([
        playbook("under_review", ["candidate", "approved"]),
      ]),
    ).toBe("approved");
  });

  it("does not skip lifecycle states for a mixed selection", () => {
    expect(
      bulkTransitionTarget([
        playbook("candidate", ["under_review"]),
        playbook("under_review", ["approved"]),
      ]),
    ).toBeNull();
  });
});
