/**
 * Pure helpers for playbook step editing.
 *
 * Merge never drops unknown keys. The patch payload sends only `step_id`
 * plus changed editor fields, so provenance (`source_refs`,
 * `grounding_status`) is never transmitted and cannot be overwritten
 * by a typed round-trip.
 */

export type EditableStep = Record<string, unknown> & { step_id: string };

export type PlaybookStepPatch = {
  step_id?: string | null;
  text?: string | null;
  title?: string | null;
  description?: string | null;
  type?: string | null;
  expected_outcome?: string | null;
  on_failure?: string | null;
  reason?: string | null;
  rollback_hint?: string | null;
  safety_class?: string | null;
  action_type?: string | null;
  action_name?: string | null;
  tool_ref?: string | null;
  requires_approval?: boolean | null;
  reversible?: boolean | null;
  verification?: boolean | null;
  time_estimate_sec?: number | null;
  clear_fields?: string[];
};

export const STEP_TYPES = [
  "diagnostic",
  "remediation",
  "verification",
  "escalation",
  "communication",
] as const;

export const EDITABLE_KEYS = [
  "text",
  "title",
  "description",
  "type",
  "expected_outcome",
  "on_failure",
  "reason",
  "rollback_hint",
  "safety_class",
  "action_type",
  "action_name",
  "tool_ref",
  "requires_approval",
  "reversible",
  "verification",
  "time_estimate_sec",
] as const;

const INSTRUCTION_KEYS = ["text", "title", "description", "action", "instruction"] as const;

function newTempId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `tmp-${Math.random().toString(36).slice(2, 12)}`;
}

export function stepInstruction(step: Record<string, unknown> | null | undefined): string {
  if (!step) return "";
  for (const key of INSTRUCTION_KEYS) {
    const value = step[key];
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return "";
}

export function ensureStepIds(steps: unknown): EditableStep[] {
  const list = Array.isArray(steps) ? steps : [];
  return list.map((raw, index) => {
    const step = raw && typeof raw === "object" ? { ...(raw as Record<string, unknown>) } : {};
    const existing = step.step_id;
    const step_id =
      typeof existing === "string" && existing.trim() ? existing : newTempId();
    return { ...step, step_id, order: index + 1 } as EditableStep;
  });
}

export function mergeStepEdit(
  step: EditableStep,
  patch: Partial<PlaybookStepPatch>,
): EditableStep {
  const next: EditableStep = { ...step };
  for (const [key, value] of Object.entries(patch)) {
    if (key === "step_id" || key === "clear_fields") continue;
    if (value === undefined) continue;
    next[key] = value;
  }
  if (Array.isArray(patch.clear_fields)) {
    for (const key of patch.clear_fields) {
      if (key === "step_id" || key === "source_refs" || key === "grounding_status") continue;
      delete next[key];
    }
  }
  return next;
}

export function moveStep(steps: EditableStep[], from: number, to: number): EditableStep[] {
  if (from < 0 || to < 0 || from >= steps.length || to >= steps.length || from === to) {
    return steps;
  }
  const next = [...steps];
  const [item] = next.splice(from, 1);
  next.splice(to, 0, item);
  return next.map((step, index) => ({ ...step, order: index + 1 }));
}

export function insertStepAfter(steps: EditableStep[], index: number): EditableStep[] {
  const insertAt = Math.min(Math.max(index + 1, 0), steps.length);
  const created: EditableStep = {
    step_id: newTempId(),
    text: "",
    type: "remediation",
    grounding_status: "non_grounded",
    step_classification: "human_authored",
    source_refs: [],
  };
  const next = [...steps];
  next.splice(insertAt, 0, created);
  return next.map((step, i) => ({ ...step, order: i + 1 }));
}

export function duplicateStep(steps: EditableStep[], index: number): EditableStep[] {
  const source = steps[index];
  if (!source) return steps;
  const copy: EditableStep = {
    ...source,
    step_id: newTempId(),
    grounding_status: "non_grounded",
    step_classification: "human_authored",
    source_refs: [],
    human_edited: undefined,
  };
  delete copy.human_edited;
  delete copy.edited_by;
  delete copy.edited_at;
  const next = [...steps];
  next.splice(index + 1, 0, copy);
  return next.map((step, i) => ({ ...step, order: i + 1 }));
}

export function removeStep(steps: EditableStep[], id: string): EditableStep[] {
  return steps
    .filter((step) => step.step_id !== id)
    .map((step, index) => ({ ...step, order: index + 1 }));
}

function sameValue(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (a == null && b == null) return true;
  try {
    return JSON.stringify(a) === JSON.stringify(b);
  } catch {
    return false;
  }
}

export function toPatchPayload(
  original: EditableStep[],
  edited: EditableStep[],
): PlaybookStepPatch[] {
  const originalById = new Map(original.map((step) => [step.step_id, step]));
  return edited.map((step) => {
    const prior = originalById.get(step.step_id);
    const patch: PlaybookStepPatch = { step_id: step.step_id };
    if (!prior) {
      for (const key of EDITABLE_KEYS) {
        const value = step[key];
        if (value !== undefined && value !== null && value !== "") {
          (patch as Record<string, unknown>)[key] = value;
        }
      }
      return patch;
    }
    for (const key of EDITABLE_KEYS) {
      if (!sameValue(step[key], prior[key])) {
        (patch as Record<string, unknown>)[key] = step[key] ?? null;
      }
    }
    return patch;
  });
}

export function diffSummary(
  original: EditableStep[],
  edited: EditableStep[],
): { added: string[]; removed: string[]; modified: string[]; reordered: boolean } {
  const originalIds = original.map((s) => s.step_id);
  const editedIds = edited.map((s) => s.step_id);
  const originalSet = new Set(originalIds);
  const editedSet = new Set(editedIds);
  const added = editedIds.filter((id) => !originalSet.has(id));
  const removed = originalIds.filter((id) => !editedSet.has(id));
  const originalById = new Map(original.map((step) => [step.step_id, step]));
  const modified: string[] = [];
  for (const step of edited) {
    const prior = originalById.get(step.step_id);
    if (!prior) continue;
    const changed = EDITABLE_KEYS.some((key) => !sameValue(step[key], prior[key]));
    if (changed) modified.push(step.step_id);
  }
  const survivingOriginal = originalIds.filter((id) => editedSet.has(id));
  const survivingEdited = editedIds.filter((id) => originalSet.has(id));
  return {
    added,
    removed,
    modified,
    reordered: survivingOriginal.join() !== survivingEdited.join(),
  };
}

export function changeCount(
  original: EditableStep[],
  edited: EditableStep[],
): number {
  const summary = diffSummary(original, edited);
  return summary.added.length + summary.removed.length + summary.modified.length + (summary.reordered ? 1 : 0);
}

/** Re-apply the user's diff onto a freshly fetched base after a 409. */
export function rebaseEdits(
  oldOriginal: EditableStep[],
  edited: EditableStep[],
  newOriginal: EditableStep[],
): EditableStep[] {
  const patches = toPatchPayload(oldOriginal, edited);
  const byId = new Map(newOriginal.map((step) => [step.step_id, step]));
  const result: EditableStep[] = [];
  for (const patch of patches) {
    const id = patch.step_id;
    if (id && byId.has(id)) {
      result.push(mergeStepEdit(byId.get(id)!, patch));
    } else {
      result.push({
        ...patch,
        step_id: id || newTempId(),
      } as EditableStep);
    }
  }
  return result.map((step, index) => ({ ...step, order: index + 1 }));
}

/** List-page `?edit=1` on an approved playbook should open the fork dialog. */
export function shouldOpenForkFromEditQuery(args: {
  wantsEdit: boolean;
  canEdit: boolean;
  lifecycleLocked: boolean;
  currentVersionIsPublished: boolean;
}): boolean {
  return (
    args.wantsEdit &&
    args.canEdit &&
    !args.lifecycleLocked &&
    args.currentVersionIsPublished
  );
}
