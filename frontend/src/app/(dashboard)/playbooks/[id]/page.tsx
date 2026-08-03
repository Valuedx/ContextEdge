"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";
import { Loader2 } from "lucide-react";

import { ApplicabilityBadge } from "@/components/common/applicability";
import { PageHeader } from "@/components/common/page-header";
import {
  DetailPageSkeleton,
  DetailWideCardSkeleton,
} from "@/components/common/detail-page-skeleton";
import { Skeleton } from "@/components/ui/skeleton";
import { StatusBadge } from "@/components/common/status-badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { api } from "@/lib/api";
import type { Playbook, PlaybookVersion, PlaybookVersionDiff } from "@/lib/types";
import { useAuthStore } from "@/lib/stores/auth-store";
import { canTransitionPlaybook } from "@/lib/roles";
import { GitCompare, RotateCcw } from "lucide-react";

// Allowed forward transitions per lifecycle state (mirrors backend VALID_TRANSITIONS)
const TRANSITIONS: Record<string, string[]> = {
  candidate: ["under_review", "retired"],
  under_review: ["approved", "candidate", "retired"],
  approved: ["deprecated", "expired", "retired"],
  restricted: ["approved", "deprecated", "retired"],
  deprecated: ["retired"],
  expired: ["under_review", "retired"],
  retired: [],
};


function TransitionDialog({
  playbook,
  onClose,
}: {
  playbook: Playbook;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const available = TRANSITIONS[playbook.lifecycle_state] ?? [];
  const [newState, setNewState] = useState(available[0] ?? "");
  const [comment, setComment] = useState("");

  const mut = useMutation({
    mutationFn: () =>
      api.post(`/playbooks/${playbook.id}/transition`, {
        new_state: newState,
        comment: comment.trim() || undefined,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["playbook", playbook.id] });
      toast.success(`Playbook transitioned to "${newState}"`);
      onClose();
    },
    onError: (err: Error) => {
      toast.error(err.message || "Transition failed");
    },
  });

  if (available.length === 0) {
    return (
      <DialogContent>
        <DialogHeader>
          <DialogTitle>No transitions available</DialogTitle>
        </DialogHeader>
        <p className="text-sm text-muted-foreground">
          Playbooks in &quot;{playbook.lifecycle_state}&quot; state cannot be transitioned further.
        </p>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Close
          </Button>
        </DialogFooter>
      </DialogContent>
    );
  }

  return (
    <DialogContent>
      <DialogHeader>
        <DialogTitle>Transition playbook</DialogTitle>
      </DialogHeader>
      <div className="space-y-4">
        <div>
          <Label>New state</Label>
          <Select value={newState} onValueChange={(v) => setNewState(v ?? "")}>
            <SelectTrigger className="mt-1">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {available.map((s) => (
                <SelectItem key={s} value={s}>
                  {s}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div>
          <Label htmlFor="comment">Comment (optional)</Label>
          <Textarea
            id="comment"
            className="mt-1"
            placeholder="Reason for this transition…"
            value={comment}
            onChange={(e) => setComment(e.target.value)}
          />
        </div>
      </div>
      <DialogFooter>
        <Button variant="outline" onClick={onClose}>
          Cancel
        </Button>
        <Button disabled={!newState || mut.isPending} onClick={() => mut.mutate()}>
          {mut.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
          Confirm
        </Button>
      </DialogFooter>
      {mut.error && (
        <p className="text-sm text-destructive">{String((mut.error as Error).message)}</p>
      )}
    </DialogContent>
  );
}

function DiffDialog({
  playbookId,
  versionId,
  onClose,
}: {
  playbookId: string;
  versionId: string;
  onClose: () => void;
}) {
  const { data, isLoading } = useQuery<PlaybookVersionDiff>({
    queryKey: ["playbook-diff", playbookId, versionId],
    queryFn: () => api.get(`/playbooks/${playbookId}/versions/${versionId}/diff`),
  });

  return (
    <DialogContent className="max-w-2xl">
      <DialogHeader>
        <DialogTitle>Version diff</DialogTitle>
      </DialogHeader>
      {isLoading ? (
        <div className="flex items-center justify-center py-8">
          <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
        </div>
      ) : data ? (
        <div className="space-y-3 text-sm">
          <p className="text-xs text-muted-foreground">
            {data.base_semantic_version
              ? `v${data.base_semantic_version} → v${data.target_semantic_version}`
              : `Initial version v${data.target_semantic_version}`}
          </p>
          {data.changed_fields.length > 0 && (
            <div className="flex flex-wrap gap-1">
              {data.changed_fields.map((f) => (
                <span key={f} className="rounded bg-muted px-2 py-0.5 text-xs">{f}</span>
              ))}
            </div>
          )}
          <pre className="max-h-80 overflow-auto rounded-md bg-muted p-3 text-xs whitespace-pre-wrap font-mono">
            {data.unified_diff || "No textual diff available."}
          </pre>
        </div>
      ) : null}
      <DialogFooter>
        <Button variant="outline" onClick={onClose}>Close</Button>
      </DialogFooter>
    </DialogContent>
  );
}

/**
 * The approved knowledge this version was generated from, each with the
 * applicability verdict as it stood at generation time.
 *
 * A reviewer asking "which SOP does this implement" needs more than a
 * list of titles: an article flagged as written for a release this
 * estate does not run still informed the playbook, and that caveat was
 * computed and shown to the model. Dropping it here would leave the
 * reviewer approving steps grounded in a document nobody told them was
 * out of scope.
 */
function KnowledgeSourcesPanel({ version }: { version: PlaybookVersion }) {
  const refs = version.evidence_refs ?? null;
  const knowledge = refs?.knowledge;

  // Versions generated before applicability was recorded carry
  // knowledge_ids but no verdicts. Say so rather than rendering a
  // verdict-less list that looks like everything checked out.
  if (!knowledge || knowledge.length === 0) {
    const count = refs?.knowledge_ids?.length ?? 0;
    if (count === 0) {
      return (
        <div className="rounded-lg border border-dashed p-4">
          <h3 className="text-sm font-semibold">Approved knowledge used</h3>
          <p className="mt-1 text-xs text-muted-foreground">
            None. This playbook reflects observed practice only — no KB
            article or SOP was matched to the pattern.
          </p>
        </div>
      );
    }
    return (
      <div className="rounded-lg border p-4">
        <h3 className="text-sm font-semibold">Approved knowledge used</h3>
        <p className="mt-1 text-xs text-muted-foreground">
          {count} document{count === 1 ? "" : "s"}. Applicability was not
          recorded for this version, so whether they match this
          environment is unknown here.
        </p>
      </div>
    );
  }

  const flagged = knowledge.filter(
    (doc) => doc.applicability_verdict === "mismatch",
  ).length;

  return (
    <div className="rounded-lg border p-4 space-y-3">
      <div>
        <h3 className="text-sm font-semibold">
          Approved knowledge used
          {flagged > 0 && (
            <span className="ml-2 rounded-full bg-amber-500/20 px-2 py-0.5 text-[10px] font-medium">
              {flagged} flagged
            </span>
          )}
        </h3>
        <p className="mt-1 text-xs text-muted-foreground">
          What the organisation says should be done, as matched to this
          pattern. A flagged document still informed the playbook — it was
          ranked lower, not withheld.
        </p>
      </div>

      <div className="space-y-2">
        {knowledge.map((doc) => (
          <div
            key={doc.evidence_id}
            className="rounded-md border bg-background p-3 space-y-1"
          >
            <div className="flex items-start justify-between gap-2">
              <Link
                href={`/evidence/${doc.evidence_id}`}
                className="text-sm font-medium hover:underline"
              >
                {doc.title || doc.evidence_id}
              </Link>
              <ApplicabilityBadge verdict={doc.applicability_verdict} />
            </div>
            {doc.evidence_type && (
              <p className="text-[11px] text-muted-foreground">
                {doc.evidence_type}
              </p>
            )}
            {doc.applicability_notes && doc.applicability_notes.length > 0 && (
              <ul className="mt-1 space-y-0.5">
                {doc.applicability_notes.map((note, index) => (
                  <li key={index} className="text-xs text-muted-foreground">
                    • {note}
                  </li>
                ))}
              </ul>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

function ConflictsPanel({ version }: { version: PlaybookVersion }) {
  const conflicts = version.conflicts;

  // null and [] mean different things and must read differently.
  // null: this version predates knowledge being an input, so the
  // comparison never ran. Rendering "no conflicts" there would claim a
  // check was performed and passed.
  if (conflicts === null || conflicts === undefined) {
    return (
      <div className="rounded-lg border border-dashed p-4">
        <h3 className="text-sm font-semibold">Documented vs. observed</h3>
        <p className="mt-1 text-xs text-muted-foreground">
          Not assessed — this version was generated before approved
          knowledge was compared against observed practice.
        </p>
      </div>
    );
  }

  if (conflicts.length === 0) {
    return (
      <div className="rounded-lg border p-4">
        <h3 className="text-sm font-semibold">Documented vs. observed</h3>
        <p className="mt-1 text-xs text-muted-foreground">
          No disagreement found between the approved procedure and what
          engineers actually did.
        </p>
      </div>
    );
  }

  return (
    <div className="rounded-lg border border-amber-500/40 bg-amber-500/5 p-4 space-y-3">
      <div>
        <h3 className="text-sm font-semibold">
          Documented vs. observed
          <span className="ml-2 rounded-full bg-amber-500/20 px-2 py-0.5 text-[10px] font-medium">
            {conflicts.length} to review
          </span>
        </h3>
        <p className="mt-1 text-xs text-muted-foreground">
          The generator did not choose between these. Preferring the SOP
          ignores runs that succeeded doing something else; preferring
          practice deletes a safeguard.
        </p>
      </div>

      <div className="space-y-3">
        {conflicts.map((conflict, index) => (
          <div key={index} className="rounded-md border bg-background p-3 space-y-2">
            {conflict.topic && (
              <p className="text-sm font-medium">{conflict.topic}</p>
            )}
            <div className="grid gap-2 sm:grid-cols-2 text-xs">
              <div>
                <span className="text-muted-foreground">Documented procedure</span>
                <p className="mt-0.5 whitespace-pre-wrap">
                  {conflict.documented || "—"}
                </p>
              </div>
              <div>
                <span className="text-muted-foreground">Observed practice</span>
                <p className="mt-0.5 whitespace-pre-wrap">
                  {conflict.observed || "—"}
                </p>
              </div>
            </div>
            {conflict.recommendation && (
              <p className="text-xs">
                <span className="text-muted-foreground">Recommended check: </span>
                {conflict.recommendation}
              </p>
            )}
            {conflict.source_refs && conflict.source_refs.length > 0 && (
              <div className="flex flex-wrap gap-1 pt-1">
                {conflict.source_refs.map((ref) => (
                  <span
                    key={ref.id}
                    title={ref.title || ref.id}
                    className="rounded border px-1.5 py-0.5 text-[10px] font-mono"
                  >
                    {ref.kind === "knowledge" ? "KB" : "EP"} {ref.title || ref.label}
                  </span>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

export default function PlaybookDetailPage() {
  const params = useParams<{ id: string }>();
  const playbookId = params.id;
  const roles = useAuthStore((s) => s.roles);
  const [transitionOpen, setTransitionOpen] = useState(false);
  const [diffVersion, setDiffVersion] = useState<string | null>(null);
  const qc = useQueryClient();

  const { data: playbook, isLoading, error } = useQuery({
    queryKey: ["playbook", playbookId],
    queryFn: () => api.get<Playbook>(`/playbooks/${playbookId}`),
    enabled: !!playbookId,
  });

  const { data: versions = [] } = useQuery({
    queryKey: ["playbook-versions", playbookId],
    queryFn: () => api.get<PlaybookVersion[]>(`/playbooks/${playbookId}/versions`),
    enabled: !!playbookId,
  });

  const rollbackMut = useMutation({
    mutationFn: (versionId: string) =>
      api.post(`/playbooks/${playbookId}/rollback`, { target_version_id: versionId }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["playbook-versions", playbookId] });
      qc.invalidateQueries({ queryKey: ["playbook", playbookId] });
      toast.success("Playbook rolled back successfully");
    },
    onError: (err: Error) => toast.error(err.message || "Rollback failed"),
  });

  if (!playbookId) return null;

  if (isLoading) {
    return (
      <DetailPageSkeleton>
        <Skeleton className="h-4 w-full max-w-xl" />
        <div className="h-px w-full bg-border" />
        <div className="space-y-4">
          <Skeleton className="h-6 w-36" />
          <DetailWideCardSkeleton lines={6} />
          <DetailWideCardSkeleton lines={6} />
        </div>
      </DetailPageSkeleton>
    );
  }

  if (error || !playbook) {
    return (
      <div className="space-y-4">
        <PageHeader title="Playbook" description="Not found." />
        <p className="text-sm text-destructive">{String((error as Error)?.message || "Missing")}</p>
        <Link href="/playbooks" className={cn(buttonVariants({ variant: "outline" }))}>
          Back to playbooks
        </Link>
      </div>
    );
  }

  const latest = versions[0];
  const hasTransitions = (TRANSITIONS[playbook.lifecycle_state] ?? []).length > 0;

  return (
    <div className="space-y-6">
      <PageHeader
        title={playbook.title}
        description={`Stable key ${playbook.stable_key} · ${playbook.automation_mode}`}
        actions={
          <div className="flex gap-2">
            {canTransitionPlaybook(roles) && hasTransitions && (
              <Button variant="outline" onClick={() => setTransitionOpen(true)}>
                Transition state
              </Button>
            )}
            <Link href="/playbooks" className={cn(buttonVariants({ variant: "outline" }))}>
              All playbooks
            </Link>
          </div>
        }
      />

      <Dialog open={transitionOpen} onOpenChange={setTransitionOpen}>
        {transitionOpen && playbook && (
          <TransitionDialog playbook={playbook} onClose={() => setTransitionOpen(false)} />
        )}
      </Dialog>

      <Dialog open={!!diffVersion} onOpenChange={(o) => { if (!o) setDiffVersion(null); }}>
        {diffVersion && playbookId && (
          <DiffDialog
            playbookId={playbookId}
            versionId={diffVersion}
            onClose={() => setDiffVersion(null)}
          />
        )}
      </Dialog>

      <div className="flex flex-wrap gap-2">
        <StatusBadge status={playbook.lifecycle_state} />
        <span className="rounded-md border px-2 py-0.5 text-xs capitalize">{playbook.risk_tier} risk</span>
      </div>

      {playbook.description && (
        <p className="text-sm text-muted-foreground whitespace-pre-wrap">{playbook.description}</p>
      )}

      <div className="grid gap-4 md:grid-cols-2 text-sm">
        <div>
          <span className="text-muted-foreground">Domain</span>{" "}
          <span className="font-mono text-xs">{playbook.domain_id ?? "—"}</span>
        </div>
        <div>
          <span className="text-muted-foreground">Last validated</span>{" "}
          {playbook.last_validated_at
            ? new Date(playbook.last_validated_at).toLocaleString()
            : "Never"}
        </div>
        {playbook.expiry_at && (
          <div>
            <span className="text-muted-foreground">Expires</span>{" "}
            {new Date(playbook.expiry_at).toLocaleString()}
          </div>
        )}
      </div>

      {latest && <KnowledgeSourcesPanel version={latest} />}
      {latest && <ConflictsPanel version={latest} />}

      <Separator />

      <div className="space-y-4">
        <h3 className="text-lg font-semibold">Versions</h3>
        {versions.length === 0 ? (
          <p className="text-sm text-muted-foreground">No published versions yet.</p>
        ) : (
          <div className="space-y-4">
            {versions.map((v, idx) => (
              <Card key={v.id}>
                <CardHeader className="pb-2">
                  <CardTitle className="text-base flex items-center justify-between gap-2">
                    <div className="flex items-center gap-2">
                      <span>v{v.semantic_version}</span>
                      {idx === 0 && (
                        <span className="rounded-full bg-primary/10 px-2 py-0.5 text-[10px] text-primary font-medium">latest</span>
                      )}
                    </div>
                    <div className="flex items-center gap-1">
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-7 px-2 text-xs"
                        onClick={() => setDiffVersion(v.id)}
                      >
                        <GitCompare className="mr-1 h-3 w-3" />
                        Diff
                      </Button>
                      {idx > 0 && canTransitionPlaybook(roles) && (
                        <Button
                          variant="ghost"
                          size="sm"
                          className="h-7 px-2 text-xs"
                          disabled={rollbackMut.isPending}
                          onClick={() => rollbackMut.mutate(v.id)}
                        >
                          <RotateCcw className="mr-1 h-3 w-3" />
                          Rollback
                        </Button>
                      )}
                      <span className="text-xs font-normal text-muted-foreground">
                        {new Date(v.created_at).toLocaleString()}
                      </span>
                    </div>
                  </CardTitle>
                </CardHeader>
                <CardContent className="space-y-3 text-sm">
                  <div>
                    <p className="text-xs text-muted-foreground mb-1">Playbook confidence</p>
                    <p>{(v.playbook_confidence * 100).toFixed(0)}%</p>
                  </div>
                  {v.execution_confidence_guidance && (
                    <p className="text-muted-foreground">{v.execution_confidence_guidance}</p>
                  )}
                  <div>
                    <p className="text-xs text-muted-foreground mb-1">Trigger conditions</p>
                    <pre className="max-h-40 overflow-auto rounded-md bg-muted p-2 text-xs">
                      {JSON.stringify(v.trigger_conditions, null, 2)}
                    </pre>
                  </div>
                  <div>
                    <p className="text-xs text-muted-foreground mb-1">Steps ({Array.isArray(v.steps) ? v.steps.length : 0})</p>
                    <pre className="max-h-48 overflow-auto rounded-md bg-muted p-2 text-xs">
                      {JSON.stringify(v.steps, null, 2)}
                    </pre>
                  </div>
                </CardContent>
              </Card>
            ))}
          </div>
        )}
      </div>

      {latest && (
        <p className="text-xs text-muted-foreground">
          Showing {versions.length} version(s). Latest is v{latest.semantic_version}.
        </p>
      )}
    </div>
  );
}
