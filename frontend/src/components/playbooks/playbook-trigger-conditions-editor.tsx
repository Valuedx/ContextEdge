"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";

const LIST_KEYS = ["symptoms", "conditions", "entities"] as const;

function asStringList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is string => typeof item === "string");
}

export function PlaybookTriggerConditionsEditor({
  value,
  onChange,
}: {
  value: Record<string, unknown>;
  onChange: (next: Record<string, unknown>) => void;
}) {
  const [rawOpen, setRawOpen] = useState(false);
  const [rawText, setRawText] = useState("");
  const [rawError, setRawError] = useState<string | null>(null);

  const setList = (key: (typeof LIST_KEYS)[number], items: string[]) => {
    onChange({ ...value, [key]: items });
  };

  const openRaw = () => {
    setRawText(JSON.stringify(value, null, 2));
    setRawError(null);
    setRawOpen(true);
  };

  const applyRaw = () => {
    try {
      const parsed = JSON.parse(rawText) as unknown;
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
        setRawError("Trigger conditions must be a JSON object.");
        return;
      }
      onChange(parsed as Record<string, unknown>);
      setRawOpen(false);
      setRawError(null);
    } catch {
      setRawError("Invalid JSON.");
    }
  };

  return (
    <div className="space-y-4">
      {LIST_KEYS.map((key) => {
        const items = asStringList(value[key]);
        return (
          <div key={key}>
            <Label className="capitalize">{key}</Label>
            <ul className="mt-1 space-y-1.5">
              {items.map((item, index) => (
                <li key={`${key}-${index}`} className="flex gap-2">
                  <Input
                    value={item}
                    onChange={(e) => {
                      const next = [...items];
                      next[index] = e.target.value;
                      setList(key, next);
                    }}
                  />
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={() => setList(key, items.filter((_, i) => i !== index))}
                  >
                    Remove
                  </Button>
                </li>
              ))}
            </ul>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="mt-1"
              onClick={() => setList(key, [...items, ""])}
            >
              Add {key.slice(0, -1)}
            </Button>
          </div>
        );
      })}
      {rawOpen ? (
        <div className="space-y-2">
          <Label htmlFor="trigger-json">Raw JSON</Label>
          <Textarea
            id="trigger-json"
            rows={8}
            className="font-mono text-xs"
            value={rawText}
            onChange={(e) => setRawText(e.target.value)}
          />
          {rawError && <p className="text-xs text-destructive">{rawError}</p>}
          <div className="flex gap-2">
            <Button type="button" size="sm" onClick={applyRaw}>
              Apply JSON
            </Button>
            <Button type="button" size="sm" variant="outline" onClick={() => setRawOpen(false)}>
              Cancel
            </Button>
          </div>
        </div>
      ) : (
        <Button type="button" variant="outline" size="sm" onClick={openRaw}>
          Edit raw JSON
        </Button>
      )}
    </div>
  );
}
