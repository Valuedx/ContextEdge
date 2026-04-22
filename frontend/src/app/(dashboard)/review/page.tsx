"use client";

/**
 * Reviewer console — Phase 5 first slice.
 *
 * Renders zones 2 (ticket header), 3 (raw user message), 5 (top decision
 * options + similar aggregate), and 7 (Approve / Reject). Zones 4 (evidence
 * cards) and 6 (plan steps) are deferred — they need separate fetches
 * (provenance endpoint for evidence, playbook version join for plan) and
 * land in the next slice. Modify verb is also deferred.
 *
 * Queue pane consumes `GET /decisions?status=pending&sort=confidence_desc`
 * so the reviewer can scan high-confidence items for bulk approval at the
 * top and focus attention on the low-confidence tail.
 *
 * Session pane consumes `GET /review-queue/{session_id}/context` — the
 * backend bundle endpoint (A5) composes session + top decision + similar
 * aggregate in one round trip, read-through cached on Redis with a warm
 * pre-fetch on session creation (C1).
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { PageHeader } from "@/components/common/page-header";
import { StatusBadge } from "@/components/common/status-badge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
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
import { cn } from "@/lib/utils";
import type {
  Decision,
  DecisionOption,
  ReviewQueueContext,
  RejectionReasonCode,
  SimilarDecisionsAggregateResponse,
} from "@/lib/types";
import {
  REJECTION_REASON_CODES,
  REJECTION_REASON_LABELS,
} from "@/lib/types";

const QUEUE_LIMIT = 50;

// ---------------------------------------------------------------------------
// Queue pane
// ---------------------------------------------------------------------------

function confidenceBadgeClasses(level: "red" | "amber" | "green" | null): string {
  // Mirrors the server-side thresholds from review_queue_service.derive_badge_level
  // so the queue cell and the session header agree on color.
  switch (level) {
    case "green":
      return "bg-emerald-500/15 text-emerald-300 border-emerald-500/30";
    case "amber":
      return "bg-amber-500/15 text-amber-300 border-amber-500/30";
    case "red":
      return "bg-rose-500/15 text-rose-300 border-rose-500/30";
    default:
      return "bg-muted text-muted-foreground border-border";
  }
}

function deriveBadgeLevel(score: number | null): "red" | "amber" | "green" | null {
  if (score == null) return null;
  if (score >= 0.8) return "green";
  if (score >= 0.5) return "amber";
  return "red";
}

function ConfidenceBadge({ score }: { score: number | null }) {
  const level = deriveBadgeLevel(score);
  return (
    <Badge
      variant="outline"
      className={cn("font-mono text-xs", confidenceBadgeClasses(level))}
    >
      {score != null ? `${Math.round(score * 100)}%` : "—"}
    </Badge>
  );
}

function QueuePane({
  selectedSessionId,
  onSelect,
}: {
  selectedSessionId: string | null;
  onSelect: (sessionId: string) => void;
}) {
  const { data = [], isLoading } = useQuery<Decision[]>({
    queryKey: ["review-queue", "pending-decisions"],
    queryFn: () =>
      api.get<Decision[]>("/decisions", {
        status: "pending",
        sort: "confidence_desc",
        limit: String(QUEUE_LIMIT),
      }),
    refetchInterval: 30_000,
  });

  // Dedupe by session_id — one ticket per session in the queue.
  // Prefer the latest pending decision per session as the representative.
  const sessions = Array.from(
    new Map(
      data
        .filter((d): d is Decision & { session_id: string } => !!d.session_id)
        .map((d) => [d.session_id, d]),
    ).values(),
  );

  return (
    <aside className="w-[340px] shrink-0 border-r bg-background/40 p-3 space-y-1 overflow-y-auto">
      <div className="px-2 pb-2 flex items-baseline justify-between">
        <h2 className="text-sm font-semibold">Queue</h2>
        <span className="text-xs text-muted-foreground">
          {isLoading ? "…" : `${sessions.length} open`}
        </span>
      </div>
      {isLoading && (
        <div className="px-2 text-xs text-muted-foreground">Loading…</div>
      )}
      {!isLoading && sessions.length === 0 && (
        <div className="px-2 py-8 text-xs text-muted-foreground text-center">
          No pending decisions.
        </div>
      )}
      <ul className="space-y-1">
        {sessions.map((d) => {
          const isActive = d.session_id === selectedSessionId;
          const level = deriveBadgeLevel(d.confidence);
          return (
            <li key={d.id}>
              <button
                type="button"
                onClick={() => onSelect(d.session_id!)}
                className={cn(
                  "w-full text-left rounded-md px-3 py-2 transition-colors flex items-start gap-2 border-l-2",
                  isActive
                    ? "bg-muted border-primary"
                    : "hover:bg-muted/60 border-transparent",
                )}
              >
                <ConfidenceBadge score={d.confidence} />
                <div className="flex-1 min-w-0">
                  <div className="text-xs font-mono truncate text-muted-foreground">
                    {d.session_id!.slice(0, 8)}…
                  </div>
                  <div className="text-sm truncate" title={d.compact_trace ?? d.rationale_summary}>
                    {d.compact_trace ?? d.rationale_summary}
                  </div>
                  <div className="text-xs text-muted-foreground flex items-center gap-2">
                    <Badge variant="outline" className="text-[10px] px-1 py-0">
                      {d.decision_type}
                    </Badge>
                    <span>{new Date(d.created_at).toLocaleDateString()}</span>
                    {level && (
                      <span className="sr-only">{level} confidence</span>
                    )}
                  </div>
                </div>
              </button>
            </li>
          );
        })}
      </ul>
    </aside>
  );
}

// ---------------------------------------------------------------------------
// Session detail pane — zones 2, 3, 5, 7
// ---------------------------------------------------------------------------

function TicketHeader({ ctx }: { ctx: ReviewQueueContext }) {
  const badge = ctx.top_decision_badge;
  return (
    <header className="space-y-2">
      <div className="flex items-start justify-between gap-4">
        <div className="space-y-1 min-w-0">
          <h2 className="text-xl font-semibold">
            Session{" "}
            <span className="font-mono text-base">
              {ctx.session.id.slice(0, 8)}
            </span>
          </h2>
          <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            <StatusBadge status={ctx.session.status} />
            <span>
              opened {new Date(ctx.session.created_at).toLocaleString()}
            </span>
            {ctx.session.external_case_ids.length > 0 && (
              <span className="font-mono">
                {ctx.session.external_case_ids.join(", ")}
              </span>
            )}
          </div>
        </div>
        {badge && (
          <div className="text-right">
            <ConfidenceBadge score={badge.score} />
            <div className="text-xs text-muted-foreground mt-1">
              top-decision confidence
            </div>
          </div>
        )}
      </div>
      {(ctx.session.symptoms.length > 0 || ctx.session.entities.length > 0) && (
        <div className="flex flex-wrap gap-1.5">
          {ctx.session.symptoms.map((s) => (
            <Badge key={`sym-${s}`} variant="secondary" className="text-[10px]">
              {s}
            </Badge>
          ))}
          {ctx.session.entities.map((e) => (
            <Badge
              key={`ent-${e}`}
              variant="outline"
              className="font-mono text-[10px]"
            >
              {e}
            </Badge>
          ))}
        </div>
      )}
    </header>
  );
}

function RawUserMessage({ notes }: { notes: string | null }) {
  if (!notes) return null;
  return (
    <Card className="bg-muted/40">
      <CardHeader className="pb-2">
        <CardTitle className="text-xs font-semibold tracking-wide uppercase text-muted-foreground">
          What the user said
        </CardTitle>
      </CardHeader>
      <CardContent>
        <p className="italic text-sm leading-relaxed">{notes}</p>
      </CardContent>
    </Card>
  );
}

function DecisionOptionRow({
  option,
  primary,
}: {
  option: DecisionOption;
  primary: boolean;
}) {
  return (
    <div
      className={cn(
        "rounded-md border p-3 text-sm space-y-1",
        primary && "border-emerald-500/40 bg-emerald-500/5",
        !primary && option.rejection_code && "opacity-60",
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="font-medium truncate">{option.action}</span>
        <div className="flex items-center gap-1.5 shrink-0">
          {option.suitability != null && (
            <Badge variant="outline" className="text-[10px] font-mono">
              {Math.round(option.suitability * 100)}%
            </Badge>
          )}
          {option.risk_level && (
            <Badge variant="outline" className="text-[10px]">
              risk {option.risk_level}
            </Badge>
          )}
          {primary && (
            <Badge className="text-[10px] bg-emerald-500/20 text-emerald-300 border-emerald-500/40">
              chosen
            </Badge>
          )}
        </div>
      </div>
      {option.rejection_code && (
        <div className="text-xs text-muted-foreground">
          ruled out:{" "}
          <Badge variant="outline" className="text-[10px]">
            {REJECTION_REASON_LABELS[option.rejection_code as RejectionReasonCode] ??
              option.rejection_code}
          </Badge>
          {option.rejection_reason && <span> — {option.rejection_reason}</span>}
        </div>
      )}
    </div>
  );
}

function RankedHypotheses({
  decision,
  similar,
}: {
  decision: Decision;
  similar: SimilarDecisionsAggregateResponse | null;
}) {
  // Selected options first (primary), then considered-but-rejected in
  // suitability-desc order. Matches the design doc's "top choice highlighted,
  // ruled-out below with reasoning."
  const sorted = [...decision.options].sort((a, b) => {
    if (a.selected !== b.selected) return a.selected ? -1 : 1;
    return (b.suitability ?? 0) - (a.suitability ?? 0);
  });
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-xs font-semibold tracking-wide uppercase text-muted-foreground">
          Ranked hypotheses
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {decision.rationale_summary && (
          <p className="text-sm text-muted-foreground">
            {decision.rationale_summary}
          </p>
        )}
        <div className="space-y-2">
          {sorted.length === 0 && (
            <div className="text-xs text-muted-foreground">
              No options recorded on this decision.
            </div>
          )}
          {sorted.map((opt) => (
            <DecisionOptionRow
              key={opt.id}
              option={opt}
              primary={opt.selected}
            />
          ))}
        </div>
        {similar && (
          <div className="pt-2 border-t text-xs text-muted-foreground">
            Based on{" "}
            <span className="font-semibold text-foreground">
              {similar.total_count}
            </span>{" "}
            similar{" "}
            {similar.decision_type.replace(/_/g, " ")} decisions
            {similar.success_rate != null && (
              <>
                {" — "}
                <span className="font-semibold text-foreground">
                  {Math.round(similar.success_rate * 100)}%
                </span>{" "}
                succeeded
              </>
            )}
            {Object.keys(similar.context_filters).length > 0 && (
              <span className="block">
                scoped to{" "}
                {Object.entries(similar.context_filters)
                  .map(([k, v]) => `${k}=${v}`)
                  .join(", ")}
              </span>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Zone 7 — decision bar (Approve + Reject; Modify deferred)
// ---------------------------------------------------------------------------

interface PendingApproval {
  runId: string;
  approvalId: string;
  requestedAction: string;
  stepRunId: string | null;
  stepInputs: Record<string, unknown>;
  stepTitle: string | null;
  safetyClass: string;
}

function findPendingApproval(ctx: ReviewQueueContext): PendingApproval | null {
  for (const run of ctx.execution_runs) {
    for (const approval of run.approval_requests) {
      if (approval.status !== "pending") continue;
      const step = approval.step_run_id
        ? run.step_runs.find((s) => s.id === approval.step_run_id) ?? null
        : null;
      return {
        runId: run.id,
        approvalId: approval.id,
        requestedAction: approval.requested_action,
        stepRunId: approval.step_run_id,
        stepInputs: (step?.inputs as Record<string, unknown>) ?? {},
        stepTitle: step?.step_title ?? null,
        safetyClass: approval.safety_class,
      };
    }
  }
  return null;
}

function RejectDialog({
  open,
  onOpenChange,
  decisionId,
  sessionId,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  decisionId: string;
  sessionId: string;
}) {
  const qc = useQueryClient();
  const [code, setCode] = useState<RejectionReasonCode>("wrong_diagnosis");
  const [comment, setComment] = useState("");
  const [error, setError] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: () =>
      api.post(`/decisions/${decisionId}/reject`, {
        code,
        comment: comment.trim() || null,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["review-queue-context", sessionId] });
      qc.invalidateQueries({ queryKey: ["review-queue", "pending-decisions"] });
      onOpenChange(false);
      setComment("");
      setError(null);
    },
    onError: (e: Error) => setError(e.message),
  });

  const commentRequired = code === "other";
  const canSubmit = !commentRequired || comment.trim().length > 0;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Reject recommendation</DialogTitle>
        </DialogHeader>
        <div className="space-y-4">
          <div className="space-y-1.5">
            <Label className="text-xs">Reason</Label>
            <Select
              value={code}
              onValueChange={(v) => setCode(v as RejectionReasonCode)}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {REJECTION_REASON_CODES.map((c) => (
                  <SelectItem key={c} value={c}>
                    {REJECTION_REASON_LABELS[c]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1.5">
            <Label className="text-xs">
              Comment {commentRequired && <span className="text-destructive">*</span>}
            </Label>
            <Textarea
              rows={3}
              value={comment}
              onChange={(e) => setComment(e.target.value)}
              placeholder={
                commentRequired
                  ? "Required when reason is 'Other'"
                  : "Optional — adds context to the rejection"
              }
            />
          </div>
          {error && (
            <div className="text-xs text-destructive">{error}</div>
          )}
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            variant="destructive"
            disabled={!canSubmit || mutation.isPending}
            onClick={() => mutation.mutate()}
          >
            {mutation.isPending ? "Rejecting…" : "Reject"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function ModifyDialog({
  open,
  onOpenChange,
  pending,
  sessionId,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  pending: PendingApproval;
  sessionId: string;
}) {
  const qc = useQueryClient();
  const [summary, setSummary] = useState("");
  const [inputsJson, setInputsJson] = useState<string>(() =>
    JSON.stringify(pending.stepInputs, null, 2),
  );
  const [code, setCode] = useState<RejectionReasonCode>("plan_incomplete");
  const [comment, setComment] = useState("");
  const [error, setError] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: () => {
      let parsedInputs: Record<string, unknown>;
      try {
        parsedInputs = JSON.parse(inputsJson);
      } catch (e) {
        throw new Error(
          `Inputs JSON is invalid: ${(e as Error).message}`,
        );
      }
      if (typeof parsedInputs !== "object" || parsedInputs === null || Array.isArray(parsedInputs)) {
        throw new Error("Inputs must be a JSON object.");
      }
      const trimmedSummary = summary.trim();
      if (!trimmedSummary) {
        throw new Error("Summary is required — it becomes the modified step's label.");
      }
      return api.post(
        `/execution/runs/${pending.runId}/approvals/${pending.approvalId}/modify`,
        {
          modification_diff: {
            inputs: parsedInputs,
            summary: trimmedSummary,
          },
          modification_reason_code: code,
          comment: comment.trim() || null,
        },
      );
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["review-queue-context", sessionId] });
      qc.invalidateQueries({ queryKey: ["review-queue", "pending-decisions"] });
      onOpenChange(false);
      setError(null);
      setSummary("");
      setComment("");
    },
    onError: (e: Error) => setError(e.message),
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>Modify plan before approving</DialogTitle>
        </DialogHeader>
        <div className="space-y-4">
          <div className="rounded-md border bg-muted/40 p-3 text-xs space-y-1">
            <div>
              <span className="text-muted-foreground">Step: </span>
              <span className="font-medium">
                {pending.stepTitle ?? pending.requestedAction}
              </span>
            </div>
            <div>
              <span className="text-muted-foreground">Safety class: </span>
              <Badge variant="outline" className="text-[10px]">
                {pending.safetyClass}
              </Badge>
            </div>
          </div>

          <div className="space-y-1.5">
            <Label className="text-xs">
              Summary <span className="text-destructive">*</span>
            </Label>
            <Textarea
              rows={2}
              value={summary}
              onChange={(e) => setSummary(e.target.value)}
              placeholder='What is changing? e.g. "shorter ttl, add notify flag"'
            />
            <p className="text-[11px] text-muted-foreground">
              Stored as the modified step&apos;s action label on the Decision&apos;s new option.
            </p>
          </div>

          <div className="space-y-1.5">
            <Label className="text-xs">Modified step inputs (JSON)</Label>
            <Textarea
              rows={8}
              value={inputsJson}
              onChange={(e) => setInputsJson(e.target.value)}
              className="font-mono text-xs"
            />
            <p className="text-[11px] text-muted-foreground">
              Merged into the step run&apos;s inputs; keys not listed here are preserved.
            </p>
          </div>

          <div className="space-y-1.5">
            <Label className="text-xs">Reason code</Label>
            <Select
              value={code}
              onValueChange={(v) => setCode(v as RejectionReasonCode)}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {REJECTION_REASON_CODES.map((c) => (
                  <SelectItem key={c} value={c}>
                    {REJECTION_REASON_LABELS[c]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="space-y-1.5">
            <Label className="text-xs">Comment (optional)</Label>
            <Textarea
              rows={2}
              value={comment}
              onChange={(e) => setComment(e.target.value)}
              placeholder="Additional context for the audit trail"
            />
          </div>

          {error && (
            <div className="text-xs text-destructive">{error}</div>
          )}
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            disabled={mutation.isPending}
            onClick={() => mutation.mutate()}
            className="bg-amber-600 hover:bg-amber-700"
          >
            {mutation.isPending ? "Submitting…" : "Approve with modifications"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}


function DecisionBar({ ctx }: { ctx: ReviewQueueContext }) {
  const qc = useQueryClient();
  const topDecision = ctx.top_decision;
  const pendingApproval = findPendingApproval(ctx);
  const [rejectOpen, setRejectOpen] = useState(false);
  const [modifyOpen, setModifyOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const approveMutation = useMutation({
    mutationFn: () => {
      if (!pendingApproval) {
        throw new Error("No pending approval to approve.");
      }
      return api.post(
        `/execution/runs/${pendingApproval.runId}/approvals/${pendingApproval.approvalId}/decide`,
        { decision: "approved", comment: null },
      );
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["review-queue-context", ctx.session.id] });
      qc.invalidateQueries({ queryKey: ["review-queue", "pending-decisions"] });
      setError(null);
    },
    onError: (e: Error) => setError(e.message),
  });

  return (
    <Card className="sticky bottom-0 bg-background/95 backdrop-blur border-t">
      <CardContent className="pt-4 pb-4 flex items-center justify-between gap-3">
        <div className="text-xs text-muted-foreground">
          {topDecision
            ? `Deciding on: ${topDecision.decision_type.replace(/_/g, " ")}`
            : "No active decision"}
        </div>
        <div className="flex items-center gap-2">
          <Button
            disabled={!pendingApproval || approveMutation.isPending}
            onClick={() => approveMutation.mutate()}
            className="bg-emerald-600 hover:bg-emerald-700"
            title={
              !pendingApproval
                ? "No pending approval request in execution runs"
                : undefined
            }
          >
            {approveMutation.isPending ? "Approving…" : "Approve"}
          </Button>
          <Button
            variant="secondary"
            disabled={!pendingApproval}
            onClick={() => setModifyOpen(true)}
            title={
              !pendingApproval
                ? "No pending approval request to modify"
                : undefined
            }
            className="bg-amber-500/15 hover:bg-amber-500/25 text-amber-200 border-amber-500/30"
          >
            Modify
          </Button>
          <Button
            variant="destructive"
            disabled={!topDecision}
            onClick={() => setRejectOpen(true)}
          >
            Reject
          </Button>
        </div>
      </CardContent>
      {error && (
        <div className="px-4 pb-3 text-xs text-destructive">{error}</div>
      )}
      {topDecision && (
        <RejectDialog
          open={rejectOpen}
          onOpenChange={setRejectOpen}
          decisionId={topDecision.id}
          sessionId={ctx.session.id}
        />
      )}
      {pendingApproval && (
        <ModifyDialog
          open={modifyOpen}
          onOpenChange={setModifyOpen}
          pending={pendingApproval}
          sessionId={ctx.session.id}
        />
      )}
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Session pane wrapper — fetches bundle + similar aggregate
// ---------------------------------------------------------------------------

function SessionDetail({ sessionId }: { sessionId: string }) {
  const { data: ctx, isLoading, error } = useQuery<ReviewQueueContext>({
    queryKey: ["review-queue-context", sessionId],
    queryFn: () => api.get<ReviewQueueContext>(`/review-queue/${sessionId}/context`),
  });

  const similarEnabled = !!ctx?.top_decision?.id;
  const { data: similar } = useQuery<SimilarDecisionsAggregateResponse>({
    queryKey: ["similar-aggregate", ctx?.top_decision?.id],
    queryFn: () =>
      api.get<SimilarDecisionsAggregateResponse>("/decisions/similar/aggregate", {
        decision_type: ctx!.top_decision!.decision_type,
        query_decision_id: ctx!.top_decision!.id,
        limit: "5",
      }),
    enabled: similarEnabled,
  });

  if (isLoading) {
    return (
      <div className="flex-1 p-6 text-sm text-muted-foreground">
        Loading session…
      </div>
    );
  }
  if (error) {
    return (
      <div className="flex-1 p-6 text-sm text-destructive">
        Failed to load session: {error.message}
      </div>
    );
  }
  if (!ctx) {
    return (
      <div className="flex-1 p-6 text-sm text-muted-foreground">
        Session not found.
      </div>
    );
  }

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      <div className="flex-1 overflow-y-auto p-6 space-y-4">
        <TicketHeader ctx={ctx} />
        <RawUserMessage notes={ctx.session.notes} />
        {/* Zone 4 (evidence cards with delta_signal color) deferred — bundle
            does not carry evidence; needs /decisions/{id}/provenance call. */}
        {ctx.top_decision ? (
          <RankedHypotheses decision={ctx.top_decision} similar={similar ?? null} />
        ) : (
          <Card>
            <CardContent className="py-6 text-sm text-muted-foreground">
              No decision recorded for this session yet.
            </CardContent>
          </Card>
        )}
        {/* Zone 6 (plan steps from PlaybookStep schema) deferred —
            requires joining the playbook version. */}
      </div>
      <DecisionBar ctx={ctx} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

// Next.js 16 App Router: useSearchParams triggers a client-side bailout
// during SSR prerender. Wrap the content in a Suspense boundary so the
// shell renders statically while the param resolves on the client —
// required for `next build` to succeed on this route.
export default function ReviewPage() {
  return (
    <Suspense fallback={<ReviewPageFallback />}>
      <ReviewPageContent />
    </Suspense>
  );
}

function ReviewPageFallback() {
  return (
    <div className="p-6 text-sm text-muted-foreground">Loading review queue…</div>
  );
}

function ReviewPageContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const selectedSessionId = searchParams.get("session");

  const handleSelect = (sessionId: string) => {
    const params = new URLSearchParams(searchParams);
    params.set("session", sessionId);
    router.replace(`/review?${params.toString()}`);
  };

  return (
    <div className="space-y-4 h-[calc(100vh-10rem)] flex flex-col">
      <PageHeader
        title="Review Queue"
        description="Confidence-ranked pending decisions. Approve, reject with structured reason, or open the full trace."
      />
      <div className="flex-1 flex border rounded-md overflow-hidden">
        <QueuePane
          selectedSessionId={selectedSessionId}
          onSelect={handleSelect}
        />
        {selectedSessionId ? (
          <SessionDetail sessionId={selectedSessionId} />
        ) : (
          <div className="flex-1 p-12 flex items-center justify-center text-sm text-muted-foreground">
            Select a ticket from the queue to review.
          </div>
        )}
      </div>
    </div>
  );
}
