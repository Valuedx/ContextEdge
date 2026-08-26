import { describe, expect, it } from "vitest";

import {
  changeCount,
  diffSummary,
  duplicateStep,
  ensureStepIds,
  insertStepAfter,
  mergeStepEdit,
  moveStep,
  rebaseEdits,
  removeStep,
  shouldOpenForkFromEditQuery,
  stepInstruction,
  toPatchPayload,
  type EditableStep,
} from "./playbook-steps";

const grounded: EditableStep = {
  step_id: "s1",
  text: "Restart the broker",
  type: "remediation",
  source_refs: [{ id: "kb-1", kind: "knowledge", label: "kb-1" }],
  grounding_status: "grounded",
  evidence_quality: "high",
  vendor_flag: true,
  order: 1,
};

describe("stepInstruction", () => {
  it("prefers text, then title, then action, then instruction", () => {
    expect(stepInstruction({ text: "A" })).toBe("A");
    expect(stepInstruction({ title: "B" })).toBe("B");
    expect(stepInstruction({ action: "C" })).toBe("C");
    expect(stepInstruction({ instruction: "D" })).toBe("D");
  });
});

describe("ensureStepIds", () => {
  it("keeps existing ids and assigns missing ones", () => {
    const out = ensureStepIds([{ step_id: "keep", text: "a" }, { text: "b" }]);
    expect(out[0].step_id).toBe("keep");
    expect(out[1].step_id).toBeTruthy();
    expect(out[1].step_id).not.toBe("keep");
  });

  it("survives a non-array", () => {
    expect(ensureStepIds({ nope: true })).toEqual([]);
  });
});

describe("mergeStepEdit", () => {
  it("preserves unknown keys including provenance", () => {
    const merged = mergeStepEdit(grounded, { text: "Restart the broker gracefully" });
    expect(merged.source_refs).toEqual(grounded.source_refs);
    expect(merged.grounding_status).toBe("grounded");
    expect(merged.vendor_flag).toBe(true);
    expect(merged.evidence_quality).toBe("high");
    expect(merged.text).toBe("Restart the broker gracefully");
  });
});

describe("moveStep", () => {
  it("rewrites order to match array position", () => {
    const steps = ensureStepIds([
      { step_id: "a", text: "one" },
      { step_id: "b", text: "two" },
      { step_id: "c", text: "three" },
    ]);
    const moved = moveStep(steps, 0, 2);
    expect(moved.map((s) => s.step_id)).toEqual(["b", "c", "a"]);
    expect(moved.map((s) => s.order)).toEqual([1, 2, 3]);
  });
});

describe("insert / duplicate / remove", () => {
  it("inserts a human-authored blank after the given index", () => {
    const steps = ensureStepIds([{ step_id: "a", text: "one" }]);
    const next = insertStepAfter(steps, 0);
    expect(next).toHaveLength(2);
    expect(next[1].grounding_status).toBe("non_grounded");
    expect(next[1].step_classification).toBe("human_authored");
    expect(next[1].source_refs).toEqual([]);
  });

  it("duplicates without copying provenance as if it were grounded", () => {
    const next = duplicateStep([grounded], 0);
    expect(next).toHaveLength(2);
    expect(next[1].step_id).not.toBe("s1");
    expect(next[1].grounding_status).toBe("non_grounded");
    expect(next[1].source_refs).toEqual([]);
  });

  it("removes by id and rewrites order", () => {
    const steps = ensureStepIds([
      { step_id: "a", text: "one" },
      { step_id: "b", text: "two" },
    ]);
    const next = removeStep(steps, "a");
    expect(next.map((s) => s.step_id)).toEqual(["b"]);
    expect(next[0].order).toBe(1);
  });
});

describe("toPatchPayload", () => {
  it("emits only changed keys plus step_id", () => {
    const original = [grounded];
    const edited = [mergeStepEdit(grounded, { text: "New wording" })];
    const payload = toPatchPayload(original, edited);
    expect(payload).toEqual([{ step_id: "s1", text: "New wording" }]);
    expect(payload[0]).not.toHaveProperty("source_refs");
    expect(payload[0]).not.toHaveProperty("grounding_status");
  });

  it("still sends unchanged steps as id-only so they are not deleted", () => {
    const original = [grounded, { ...grounded, step_id: "s2", text: "Other" }];
    const edited = [mergeStepEdit(grounded, { text: "New wording" }), original[1]];
    const payload = toPatchPayload(original, edited);
    expect(payload[1]).toEqual({ step_id: "s2" });
  });
});

describe("diffSummary", () => {
  it("counts add, remove, modify and reorder", () => {
    const original = ensureStepIds([
      { step_id: "a", text: "one" },
      { step_id: "b", text: "two" },
    ]);
    const edited = [
      { ...original[1], text: "two changed" },
      { step_id: "c", text: "new" },
    ];
    const summary = diffSummary(original, edited);
    expect(summary.added).toEqual(["c"]);
    expect(summary.removed).toEqual(["a"]);
    expect(summary.modified).toEqual(["b"]);
    expect(summary.reordered).toBe(false);
    expect(changeCount(original, edited)).toBeGreaterThan(0);
  });

  it("detects reorder of surviving ids", () => {
    const original = ensureStepIds([
      { step_id: "a", text: "one" },
      { step_id: "b", text: "two" },
    ]);
    const summary = diffSummary(original, [original[1], original[0]]);
    expect(summary.reordered).toBe(true);
    expect(summary.modified).toEqual([]);
  });
});

describe("shouldOpenForkFromEditQuery", () => {
  it("opens the fork dialog only for ?edit=1 on a published current version", () => {
    expect(
      shouldOpenForkFromEditQuery({
        wantsEdit: true,
        canEdit: true,
        lifecycleLocked: false,
        currentVersionIsPublished: true,
      }),
    ).toBe(true);
    expect(
      shouldOpenForkFromEditQuery({
        wantsEdit: true,
        canEdit: true,
        lifecycleLocked: false,
        currentVersionIsPublished: false,
      }),
    ).toBe(false);
    expect(
      shouldOpenForkFromEditQuery({
        wantsEdit: false,
        canEdit: true,
        lifecycleLocked: false,
        currentVersionIsPublished: true,
      }),
    ).toBe(false);
  });
});

describe("rebaseEdits", () => {
  it("replays the user diff onto a newer base", () => {
    const oldOriginal = ensureStepIds([{ step_id: "a", text: "one", source_refs: [1] }]);
    const edited = [{ ...oldOriginal[0], text: "one changed" }];
    const newOriginal = ensureStepIds([
      { step_id: "a", text: "one from other editor", source_refs: [1] },
    ]);
    const rebased = rebaseEdits(oldOriginal, edited, newOriginal);
    expect(rebased[0].text).toBe("one changed");
    expect(rebased[0].source_refs).toEqual([1]);
  });
});
