"use client";

import { Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";

export function PlaybookSaveBar({
  changeCount,
  editNote,
  onEditNoteChange,
  onSave,
  onDiscard,
  saving,
  conflict,
  onReload,
  onOverwrite,
}: {
  changeCount: number;
  editNote: string;
  onEditNoteChange: (value: string) => void;
  onSave: () => void;
  onDiscard: () => void;
  saving: boolean;
  conflict: boolean;
  onReload: () => void;
  onOverwrite: () => void;
}) {
  return (
    <div className="sticky bottom-0 z-20 border-t bg-card/95 p-4 shadow-[0_-8px_24px_rgba(15,23,42,0.08)] backdrop-blur">
      {conflict && (
        <div className="mb-3 rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-sm">
          <p className="font-medium">This draft was edited elsewhere.</p>
          <p className="mt-1 text-xs text-muted-foreground">
            Reload the latest draft, or re-apply your changes on top of it. A blind overwrite is
            not allowed.
          </p>
          <div className="mt-2 flex gap-2">
            <Button type="button" size="sm" variant="outline" onClick={onReload}>
              Reload latest
            </Button>
            <Button type="button" size="sm" onClick={onOverwrite}>
              Keep mine and retry
            </Button>
          </div>
        </div>
      )}
      <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-end">
        <div>
          <Label htmlFor="edit-note">Why did this change? (optional)</Label>
          <Textarea
            id="edit-note"
            className="mt-1"
            rows={2}
            maxLength={500}
            placeholder="Short note for the audit trail — what you changed and why."
            value={editNote}
            onChange={(e) => onEditNoteChange(e.target.value)}
          />
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs text-muted-foreground">
            {changeCount} unsaved change{changeCount === 1 ? "" : "s"}
          </span>
          <Button type="button" variant="outline" onClick={onDiscard} disabled={saving}>
            Discard
          </Button>
          <Button type="button" onClick={onSave} disabled={saving || changeCount === 0}>
            {saving && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
            Save draft
          </Button>
        </div>
      </div>
    </div>
  );
}
