"use client";

import { useState } from "react";

import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { STEP_TYPES, mergeStepEdit, type EditableStep } from "@/lib/playbook-steps";

const SAFETY_CLASSES = ["read_only", "low_side_effect", "high_side_effect", "destructive"];
const ACTION_TYPES = [
  "diagnostic",
  "remediation",
  "notification",
  "escalation",
  "approval",
  "manual",
];

function prettyEnum(value: string | null | undefined): string {
  if (!value || value === "__none__") return "None";
  return value.replace(/_/g, " ");
}

export function PlaybookStepEditor({
  step,
  onChange,
}: {
  step: EditableStep;
  onChange: (next: EditableStep) => void;
}) {
  const [advanced, setAdvanced] = useState(false);
  const instruction =
    typeof step.text === "string"
      ? step.text
      : typeof step.title === "string"
        ? step.title
        : "";
  const refs = Array.isArray(step.source_refs) ? step.source_refs : [];

  const set = (patch: Record<string, unknown>) => onChange(mergeStepEdit(step, patch));

  return (
    <div className="space-y-3">
      <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_10rem]">
        <div>
          <Label htmlFor={`step-text-${step.step_id}`}>Instruction</Label>
          <Textarea
            id={`step-text-${step.step_id}`}
            className="mt-1"
            rows={3}
            value={instruction}
            onChange={(e) => set({ text: e.target.value })}
          />
        </div>
        <div>
          <Label>Type</Label>
          <Select
            value={typeof step.type === "string" && step.type ? step.type : "remediation"}
            onValueChange={(v) => set({ type: v ?? "remediation" })}
          >
            <SelectTrigger className="mt-1 w-full">
              <span className="truncate capitalize">
                {prettyEnum(
                  typeof step.type === "string" && step.type ? step.type : "remediation",
                )}
              </span>
            </SelectTrigger>
            <SelectContent>
              {STEP_TYPES.map((type) => (
                <SelectItem key={type} value={type}>
                  {type}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <div>
          <Label htmlFor={`step-out-${step.step_id}`}>Expected outcome</Label>
          <Textarea
            id={`step-out-${step.step_id}`}
            className="mt-1"
            rows={2}
            value={typeof step.expected_outcome === "string" ? step.expected_outcome : ""}
            onChange={(e) => set({ expected_outcome: e.target.value })}
          />
        </div>
        <div>
          <Label htmlFor={`step-fail-${step.step_id}`}>On failure</Label>
          <Textarea
            id={`step-fail-${step.step_id}`}
            className="mt-1"
            rows={2}
            value={typeof step.on_failure === "string" ? step.on_failure : ""}
            onChange={(e) => set({ on_failure: e.target.value })}
          />
        </div>
      </div>

      <div>
        <Label htmlFor={`step-reason-${step.step_id}`}>Reason</Label>
        <Input
          id={`step-reason-${step.step_id}`}
          className="mt-1"
          value={typeof step.reason === "string" ? step.reason : ""}
          onChange={(e) => set({ reason: e.target.value })}
        />
      </div>

      <div className="flex flex-wrap items-center gap-1.5">
        {typeof step.grounding_status === "string" && (
          <span className="rounded border px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-muted-foreground">
            {step.grounding_status}
          </span>
        )}
        {refs.map((ref, i) => {
          const r = ref as { id?: string; label?: string; title?: string };
          return (
            <span
              key={r.id || i}
              className="rounded border bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground"
            >
              {r.label || r.title || "source"}
            </span>
          );
        })}
      </div>

      <button
        type="button"
        className="text-xs font-medium text-primary hover:underline"
        onClick={() => setAdvanced((v) => !v)}
      >
        {advanced ? "Hide advanced" : "Advanced"}
      </button>

      {advanced && (
        <div className="grid gap-3 rounded-md border bg-muted/30 p-3 sm:grid-cols-2">
          <div>
            <Label>Safety class</Label>
            <Select
              value={typeof step.safety_class === "string" ? step.safety_class : "__none__"}
              onValueChange={(v) =>
                set({ safety_class: !v || v === "__none__" ? null : v })
              }
            >
            <SelectTrigger className="mt-1 w-full">
              <span className="truncate capitalize">
                {prettyEnum(typeof step.safety_class === "string" ? step.safety_class : null)}
              </span>
            </SelectTrigger>
              <SelectContent>
                <SelectItem value="__none__">None</SelectItem>
                {SAFETY_CLASSES.map((value) => (
                  <SelectItem key={value} value={value}>
                    {value}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div>
            <Label>Action type</Label>
            <Select
              value={typeof step.action_type === "string" ? step.action_type : "__none__"}
              onValueChange={(v) =>
                set({ action_type: !v || v === "__none__" ? null : v })
              }
            >
            <SelectTrigger className="mt-1 w-full">
              <span className="truncate capitalize">
                {prettyEnum(typeof step.action_type === "string" ? step.action_type : null)}
              </span>
            </SelectTrigger>
              <SelectContent>
                <SelectItem value="__none__">None</SelectItem>
                {ACTION_TYPES.map((value) => (
                  <SelectItem key={value} value={value}>
                    {value}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div>
            <Label htmlFor={`step-an-${step.step_id}`}>Action name</Label>
            <Input
              id={`step-an-${step.step_id}`}
              className="mt-1"
              value={typeof step.action_name === "string" ? step.action_name : ""}
              onChange={(e) => set({ action_name: e.target.value })}
            />
          </div>
          <div>
            <Label htmlFor={`step-tool-${step.step_id}`}>Tool ref</Label>
            <Input
              id={`step-tool-${step.step_id}`}
              className="mt-1"
              value={typeof step.tool_ref === "string" ? step.tool_ref : ""}
              onChange={(e) => set({ tool_ref: e.target.value })}
            />
          </div>
          <div>
            <Label htmlFor={`step-time-${step.step_id}`}>Time estimate (sec)</Label>
            <Input
              id={`step-time-${step.step_id}`}
              type="number"
              min={0}
              max={86400}
              className="mt-1"
              value={typeof step.time_estimate_sec === "number" ? step.time_estimate_sec : ""}
              onChange={(e) =>
                set({
                  time_estimate_sec: e.target.value === "" ? null : Number(e.target.value),
                })
              }
            />
          </div>
          <div>
            <Label htmlFor={`step-rb-${step.step_id}`}>Rollback hint</Label>
            <Input
              id={`step-rb-${step.step_id}`}
              className="mt-1"
              value={typeof step.rollback_hint === "string" ? step.rollback_hint : ""}
              onChange={(e) => set({ rollback_hint: e.target.value })}
            />
          </div>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={step.requires_approval === true}
              onChange={(e) => set({ requires_approval: e.target.checked })}
            />
            Requires approval
          </label>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={step.reversible === true}
              onChange={(e) => set({ reversible: e.target.checked })}
            />
            Reversible
          </label>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={step.verification === true}
              onChange={(e) => set({ verification: e.target.checked })}
            />
            Verification step
          </label>
        </div>
      )}
    </div>
  );
}
