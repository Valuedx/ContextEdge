"use client";

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Loader2, LockKeyhole, Send, Shuffle } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
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
import { canTransitionPlaybook } from "@/lib/roles";
import { useAuthStore } from "@/lib/stores/auth-store";
import type { Playbook } from "@/lib/types";

const STATE_LABELS: Record<string, string> = {
  candidate: "Candidate",
  under_review: "Under review",
  approved: "Approved",
  restricted: "Restricted",
  deprecated: "Deprecated",
  expired: "Expired",
  retired: "Retired",
};

export function lifecycleStateLabel(state: string): string {
  return STATE_LABELS[state] ?? state.replaceAll("_", " ");
}

export function primaryTransition(playbook: Playbook): string {
  const available = playbook.allowed_transitions ?? [];
  if (playbook.lifecycle_state === "candidate" && available.includes("under_review")) {
    return "under_review";
  }
  if (playbook.lifecycle_state === "under_review" && available.includes("approved")) {
    return "approved";
  }
  return available[0] ?? "";
}

export function bulkTransitionTarget(playbooks: Playbook[]): string | null {
  if (playbooks.length === 0) return null;
  const states = new Set(playbooks.map((playbook) => playbook.lifecycle_state));
  if (states.size !== 1) return null;
  const state = playbooks[0].lifecycle_state;
  const target = state === "candidate" ? "under_review" : state === "under_review" ? "approved" : null;
  if (!target) return null;
  return playbooks.every((playbook) => playbook.allowed_transitions?.includes(target))
    ? target
    : null;
}

export function transitionPayload(newState: string, comment: string) {
  return {
    new_state: newState,
    comments: comment.trim() || undefined,
  };
}

function actionPresentation(playbook: Playbook, target: string) {
  if (playbook.lifecycle_state === "candidate" && target === "under_review") {
    return { label: "Submit for review", Icon: Send };
  }
  if (playbook.lifecycle_state === "under_review" && target === "approved") {
    return { label: "Approve", Icon: CheckCircle2 };
  }
  return { label: "Change state", Icon: Shuffle };
}

export function PlaybookLifecycleActions({
  playbook,
  showPermissionHint = false,
}: {
  playbook: Playbook;
  showPermissionHint?: boolean;
}) {
  const roles = useAuthStore((state) => state.roles);
  const queryClient = useQueryClient();
  const available = playbook.allowed_transitions ?? [];
  const initialTarget = primaryTransition(playbook);
  const [open, setOpen] = useState(false);
  const [newState, setNewState] = useState(initialTarget);
  const [comments, setComments] = useState("");
  const permitted = canTransitionPlaybook(roles);
  const presentation = actionPresentation(playbook, initialTarget);
  const ActionIcon = presentation.Icon;

  const transition = useMutation({
    mutationFn: () =>
      api.post<Playbook>(
        `/playbooks/${playbook.id}/transition`,
        transitionPayload(newState, comments),
      ),
    onSuccess: (updated) => {
      queryClient.setQueryData(["playbook", playbook.id], updated);
      queryClient.invalidateQueries({ queryKey: ["playbooks"] });
      toast.success(`Playbook is now ${lifecycleStateLabel(updated.lifecycle_state)}`);
      setOpen(false);
      setComments("");
    },
    onError: (error: Error) => toast.error(error.message || "Playbook transition failed"),
  });

  if (available.length === 0) return null;

  if (!permitted) {
    return (
      <div className="flex flex-col items-start gap-1">
        <span title="Ask a tenant administrator to assign the playbook_reviewer role in Settings > Users.">
          <Button variant="outline" size="sm" disabled>
            <LockKeyhole className="h-4 w-4" />
            Approval locked
          </Button>
        </span>
        {showPermissionHint && (
          <span className="max-w-sm text-xs text-muted-foreground">
            The playbook_reviewer role is required. A tenant administrator can assign it in
            Settings &gt; Users.
          </span>
        )}
      </div>
    );
  }

  return (
    <>
      <Button
        variant={initialTarget === "approved" ? "default" : "outline"}
        size="sm"
        onClick={() => {
          setNewState(primaryTransition(playbook));
          setOpen(true);
        }}
      >
        <ActionIcon className="h-4 w-4" />
        {presentation.label}
      </Button>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{presentation.label}</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <div className="rounded-md border bg-muted/40 px-3 py-2 text-sm">
              <span className="font-medium">{lifecycleStateLabel(playbook.lifecycle_state)}</span>
              <span className="mx-2 text-muted-foreground">to</span>
              <span className="font-medium">{lifecycleStateLabel(newState)}</span>
            </div>
            {available.length > 1 && (
              <div>
                <Label htmlFor={`state-${playbook.id}`}>New state</Label>
                <Select value={newState} onValueChange={(value) => setNewState(value ?? "")}>
                  <SelectTrigger id={`state-${playbook.id}`} className="mt-1">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {available.map((state) => (
                      <SelectItem key={state} value={state}>
                        {lifecycleStateLabel(state)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            )}
            {playbook.lifecycle_state === "candidate" && (
              <p className="text-sm text-muted-foreground">
                This submits the candidate for review. After it moves to Under review, the
                Approve action becomes available.
              </p>
            )}
            <div>
              <Label htmlFor={`review-comments-${playbook.id}`}>Review note (optional)</Label>
              <Textarea
                id={`review-comments-${playbook.id}`}
                className="mt-1"
                value={comments}
                onChange={(event) => setComments(event.target.value)}
                placeholder="Evidence checked, limitations, or reason for this decision"
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button
              disabled={!newState || transition.isPending}
              onClick={() => transition.mutate()}
            >
              {transition.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
              Confirm {lifecycleStateLabel(newState)}
            </Button>
          </DialogFooter>
          {transition.error && (
            <p className="text-sm text-destructive">{transition.error.message}</p>
          )}
        </DialogContent>
      </Dialog>
    </>
  );
}
