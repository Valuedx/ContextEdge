"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";
import { CheckCircle2, XCircle } from "lucide-react";

import { PageHeader } from "@/components/common/page-header";
import { DataTableSkeleton } from "@/components/common/data-table-skeleton";
import { StatusBadge } from "@/components/common/status-badge";
import { Button } from "@/components/ui/button";
import {
  Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { api } from "@/lib/api";
import type { PendingApproval } from "@/lib/types";

// ── Decide Dialog ────────────────────────────────────────────────────────────

function DecideDialog({
  approval,
  onClose,
}: {
  approval: PendingApproval;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const [decision, setDecision] = useState<"approved" | "denied">("approved");
  const [comment, setComment] = useState("");

  const mut = useMutation({
    mutationFn: () =>
      api.post(
        `/execution/runs/${approval.execution_run_id}/approvals/${approval.id}/decide`,
        { decision, comment: comment.trim() || null }
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["pending-approvals"] });
      toast.success(`Request ${decision}`);
      onClose();
    },
    onError: (err: Error) => toast.error(err.message || "Decision failed"),
  });

  return (
    <DialogContent>
      <DialogHeader>
        <DialogTitle>Review approval request</DialogTitle>
      </DialogHeader>
      <div className="space-y-4 text-sm">
        <div className="rounded-md bg-muted px-3 py-2 space-y-1">
          <p className="text-xs text-muted-foreground">Action requested</p>
          <p className="font-medium break-all">{approval.requested_action}</p>
        </div>
        <div className="flex items-center gap-2">
          <Badge variant="outline" className="text-xs">{approval.safety_class}</Badge>
          <StatusBadge status={approval.status} />
        </div>
        {Object.keys(approval.context).length > 0 && (
          <div>
            <p className="text-xs text-muted-foreground mb-1">Context</p>
            <pre className="rounded-md bg-muted p-2 text-xs max-h-36 overflow-auto">
              {JSON.stringify(approval.context, null, 2)}
            </pre>
          </div>
        )}
        <div>
          <Label>Decision</Label>
          <div className="mt-1 flex gap-2">
            <Button
              size="sm"
              variant={decision === "approved" ? "default" : "outline"}
              onClick={() => setDecision("approved")}
            >
              <CheckCircle2 className="mr-1 h-3.5 w-3.5" />
              Approve
            </Button>
            <Button
              size="sm"
              variant={decision === "denied" ? "destructive" : "outline"}
              onClick={() => setDecision("denied")}
            >
              <XCircle className="mr-1 h-3.5 w-3.5" />
              Deny
            </Button>
          </div>
        </div>
        <div>
          <Label htmlFor="ap-comment">Comment (optional)</Label>
          <Textarea
            id="ap-comment"
            className="mt-1"
            rows={2}
            value={comment}
            onChange={(e) => setComment(e.target.value)}
          />
        </div>
      </div>
      <DialogFooter>
        <Button variant="outline" onClick={onClose}>Cancel</Button>
        <Button
          disabled={mut.isPending}
          variant={decision === "denied" ? "destructive" : "default"}
          onClick={() => mut.mutate()}
        >
          {mut.isPending ? "Submitting…" : `Confirm ${decision}`}
        </Button>
      </DialogFooter>
    </DialogContent>
  );
}

// ── Page ────────────────────────────────────────────────────────────────────

export default function ExecutionPage() {
  const [deciding, setDeciding] = useState<PendingApproval | null>(null);

  const { data: approvals = [], isLoading } = useQuery<PendingApproval[]>({
    queryKey: ["pending-approvals"],
    queryFn: () => api.get("/execution/approvals/pending"),
    refetchInterval: 30_000,
  });

  return (
    <div className="space-y-6">
      <PageHeader
        title="Execution approvals"
        description="Pending human-in-the-loop approval requests from automated playbook runs."
      />

      {isLoading ? (
        <DataTableSkeleton columns={4} />
      ) : approvals.length === 0 ? (
        <div className="rounded-md border p-10 text-center text-sm text-muted-foreground">
          No pending approvals.
        </div>
      ) : (
        <div className="space-y-3">
          {approvals.map((ap) => (
            <div key={ap.id} className="rounded-md border px-4 py-3 text-sm">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="space-y-1 min-w-0">
                  <p className="font-medium break-all">{ap.requested_action}</p>
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge variant="outline" className="text-xs">{ap.safety_class}</Badge>
                    <StatusBadge status={ap.status} />
                    <span className="font-mono text-xs text-muted-foreground">
                      run {ap.execution_run_id}
                    </span>
                  </div>
                  <p className="text-xs text-muted-foreground">
                    {new Date(ap.created_at).toLocaleString()}
                  </p>
                </div>
                <Button size="sm" onClick={() => setDeciding(ap)}>
                  Review
                </Button>
              </div>
            </div>
          ))}
        </div>
      )}

      <Dialog open={!!deciding} onOpenChange={(o) => { if (!o) setDeciding(null); }}>
        {deciding && (
          <DecideDialog approval={deciding} onClose={() => setDeciding(null)} />
        )}
      </Dialog>
    </div>
  );
}
