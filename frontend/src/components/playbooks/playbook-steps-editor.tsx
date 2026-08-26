"use client";

import { useEffect } from "react";
import { ChevronDown, ChevronUp, Copy, Plus, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { PlaybookStepEditor } from "@/components/playbooks/playbook-step-editor";
import { Button } from "@/components/ui/button";
import {
  duplicateStep,
  insertStepAfter,
  moveStep,
  removeStep,
  stepInstruction,
  type EditableStep,
} from "@/lib/playbook-steps";

export function PlaybookStepsEditor({
  steps,
  onChange,
}: {
  steps: EditableStep[];
  onChange: (next: EditableStep[]) => void;
}) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (!event.altKey) return;
      if (event.key !== "ArrowUp" && event.key !== "ArrowDown") return;
      const active = document.activeElement;
      const card = active instanceof HTMLElement ? active.closest("[data-step-index]") : null;
      if (!card) return;
      const index = Number(card.getAttribute("data-step-index"));
      if (Number.isNaN(index)) return;
      event.preventDefault();
      onChange(moveStep(steps, index, event.key === "ArrowUp" ? index - 1 : index + 1));
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [steps, onChange]);

  if (steps.length === 0) {
    return (
      <div className="rounded-lg border border-dashed p-6 text-center">
        <p className="text-sm text-muted-foreground">
          No steps yet. An empty draft can be saved, but it cannot go to review.
        </p>
        <Button className="mt-3" size="sm" onClick={() => onChange(insertStepAfter([], -1))}>
          <Plus className="mr-1 h-3.5 w-3.5" />
          Add first step
        </Button>
      </div>
    );
  }

  return (
    <ol className="space-y-3">
      {steps.map((step, index) => (
        <li
          key={step.step_id}
          data-step-index={index}
          className="rounded-lg border bg-card p-4 shadow-sm"
        >
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
            <span className="font-mono text-xs text-muted-foreground">Step {index + 1}</span>
            <div className="flex items-center gap-1">
              <Button
                type="button"
                variant="ghost"
                size="icon-xs"
                aria-label="Move step up"
                disabled={index === 0}
                onClick={() => onChange(moveStep(steps, index, index - 1))}
              >
                <ChevronUp />
              </Button>
              <Button
                type="button"
                variant="ghost"
                size="icon-xs"
                aria-label="Move step down"
                disabled={index === steps.length - 1}
                onClick={() => onChange(moveStep(steps, index, index + 1))}
              >
                <ChevronDown />
              </Button>
              <Button
                type="button"
                variant="ghost"
                size="icon-xs"
                aria-label="Duplicate step"
                onClick={() => onChange(duplicateStep(steps, index))}
              >
                <Copy />
              </Button>
              <Button
                type="button"
                variant="ghost"
                size="icon-xs"
                aria-label="Add step after"
                onClick={() => onChange(insertStepAfter(steps, index))}
              >
                <Plus />
              </Button>
              <Button
                type="button"
                variant="ghost"
                size="icon-xs"
                aria-label="Delete step"
                onClick={() => {
                  const label = stepInstruction(step) || `Step ${index + 1}`;
                  const previous = steps;
                  onChange(removeStep(steps, step.step_id));
                  toast.success("Step removed", {
                    description: label.slice(0, 120),
                    action: {
                      label: "Undo",
                      onClick: () => onChange(previous),
                    },
                  });
                }}
              >
                <Trash2 />
              </Button>
            </div>
          </div>
          <PlaybookStepEditor
            step={step}
            onChange={(next) => {
              const copy = [...steps];
              copy[index] = next;
              onChange(copy);
            }}
          />
        </li>
      ))}
    </ol>
  );
}
