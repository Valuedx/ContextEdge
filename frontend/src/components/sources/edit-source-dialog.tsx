"use client";

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { toast } from "sonner";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { api } from "@/lib/api";
import type { Source } from "@/lib/types";

interface EditSourceDialogProps {
  source: Source;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function EditSourceDialog({
  source,
  open,
  onOpenChange,
}: EditSourceDialogProps) {
  const queryClient = useQueryClient();
  const [displayName, setDisplayName] = useState(source.display_name);
  const [purpose, setPurpose] = useState(source.purpose ?? "");
  const [syncMode, setSyncMode] = useState(source.sync_mode || "incremental");
  const [isActive, setIsActive] = useState(source.is_active);
  const [configText, setConfigText] = useState(
    JSON.stringify(source.config ?? {}, null, 2),
  );

  const mutation = useMutation({
    mutationFn: async () => {
      const trimmedName = displayName.trim();
      if (!trimmedName) {
        throw new Error("Display name is required");
      }

      let config: Record<string, unknown>;
      try {
        config = configText.trim() ? JSON.parse(configText) : {};
      } catch {
        throw new Error("Config must be valid JSON");
      }

      return api.patch<Source>(`/sources/${source.id}`, {
        display_name: trimmedName,
        purpose: purpose.trim() || null,
        sync_mode: syncMode,
        is_active: isActive,
        config,
      });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["sources"] });
      onOpenChange(false);
      toast.success("Source updated");
    },
    onError: (error: Error) => {
      toast.error(error.message || "Failed to update source");
    },
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[560px]">
        <DialogHeader>
          <DialogTitle>Edit Source</DialogTitle>
          <DialogDescription>
            Update source display details and ingestion configuration.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="edit-source-name">Display Name</Label>
            <Input
              id="edit-source-name"
              value={displayName}
              onChange={(event) => setDisplayName(event.target.value)}
            />
          </div>

          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-2">
              <Label>Source Type</Label>
              <Input value={source.source_type} disabled />
            </div>

            <div className="space-y-2">
              <Label>Sync Mode</Label>
              <Select value={syncMode} onValueChange={setSyncMode}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="incremental">Incremental</SelectItem>
                  <SelectItem value="backfill">Backfill</SelectItem>
                  <SelectItem value="manual">Manual</SelectItem>
                  <SelectItem value="disabled">Disabled</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>

          <div className="space-y-2">
            <Label>Status</Label>
            <Select
              value={isActive ? "active" : "inactive"}
              onValueChange={(value) => setIsActive(value === "active")}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="active">Active</SelectItem>
                <SelectItem value="inactive">Inactive</SelectItem>
              </SelectContent>
            </Select>
          </div>

          <div className="space-y-2">
            <Label htmlFor="edit-source-purpose">Purpose</Label>
            <Textarea
              id="edit-source-purpose"
              value={purpose}
              onChange={(event) => setPurpose(event.target.value)}
              placeholder="Describe what this source is for..."
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="edit-source-config">Config JSON</Label>
            <Textarea
              id="edit-source-config"
              value={configText}
              onChange={(event) => setConfigText(event.target.value)}
              className="min-h-36 font-mono text-xs"
            />
          </div>
        </div>

        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            onClick={() => onOpenChange(false)}
          >
            Cancel
          </Button>
          <Button
            type="button"
            disabled={mutation.isPending}
            onClick={() => mutation.mutate()}
          >
            {mutation.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
            Update Source
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
