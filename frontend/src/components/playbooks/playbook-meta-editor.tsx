"use client";

import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";

import { PlaybookTriggerConditionsEditor } from "./playbook-trigger-conditions-editor";

const RISK_TIERS = ["minimal", "low", "medium", "high", "critical", "restricted"];

export type PlaybookMetaDraft = {
  title: string;
  description: string;
  risk_tier: string;
  rollback_notes: string;
  execution_confidence_guidance: string;
  trigger_conditions: Record<string, unknown>;
};

export function PlaybookMetaEditor({
  value,
  onChange,
}: {
  value: PlaybookMetaDraft;
  onChange: (next: PlaybookMetaDraft) => void;
}) {
  const set = (patch: Partial<PlaybookMetaDraft>) => onChange({ ...value, ...patch });

  return (
    <div className="space-y-4">
      <div>
        <Label htmlFor="pb-title">Title</Label>
        <Input
          id="pb-title"
          className="mt-1"
          value={value.title}
          onChange={(e) => set({ title: e.target.value })}
        />
      </div>
      <div>
        <Label htmlFor="pb-desc">Description</Label>
        <Textarea
          id="pb-desc"
          className="mt-1"
          rows={3}
          value={value.description}
          onChange={(e) => set({ description: e.target.value })}
        />
      </div>
      <div>
        <Label>Risk tier</Label>
        <Select value={value.risk_tier} onValueChange={(v) => set({ risk_tier: v ?? "medium" })}>
            <SelectTrigger className="mt-1 w-full">
              <span className="truncate capitalize">
                {value.risk_tier.replace(/_/g, " ")}
              </span>
            </SelectTrigger>
          <SelectContent>
            {RISK_TIERS.map((tier) => (
              <SelectItem key={tier} value={tier}>
                {tier}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      <PlaybookTriggerConditionsEditor
        value={value.trigger_conditions}
        onChange={(trigger_conditions) => set({ trigger_conditions })}
      />
      <div>
        <Label htmlFor="pb-rollback">Rollback notes</Label>
        <Textarea
          id="pb-rollback"
          className="mt-1"
          rows={3}
          value={value.rollback_notes}
          onChange={(e) => set({ rollback_notes: e.target.value })}
        />
      </div>
      <div>
        <Label htmlFor="pb-guidance">Execution confidence guidance</Label>
        <Textarea
          id="pb-guidance"
          className="mt-1"
          rows={2}
          value={value.execution_confidence_guidance}
          onChange={(e) => set({ execution_confidence_guidance: e.target.value })}
        />
      </div>
    </div>
  );
}
