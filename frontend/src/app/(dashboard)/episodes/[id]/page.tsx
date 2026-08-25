"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useParams } from "next/navigation";
import { AlertTriangle, CheckCircle2, Loader2, Pencil, X, Check } from "lucide-react";
import { useState } from "react";

import { PageHeader } from "@/components/common/page-header";
import {
  DetailCardGridSkeleton,
  DetailPageSkeleton,
} from "@/components/common/detail-page-skeleton";
import { Skeleton } from "@/components/ui/skeleton";
import { StatusBadge } from "@/components/common/status-badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { Textarea } from "@/components/ui/textarea";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";
import type { EpisodeDetail, EpisodeStep } from "@/lib/types";
import { useAuthStore } from "@/lib/stores/auth-store";
import { canApproveEpisode } from "@/lib/roles";

function StepCard({
  step,
  episodeId,
  canEdit,
}: {
  step: EpisodeStep;
  episodeId: string;
  canEdit: boolean;
}) {
  const qc = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(step.text);
  const [observation, setObservation] = useState(step.observation ?? "");

  const mut = useMutation({
    mutationFn: () =>
      api.patch(`/episodes/${episodeId}/steps/${step.id}`, {
        text: text.trim() || undefined,
        observation: observation.trim() || null,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["episode", episodeId] });
      setEditing(false);
    },
  });

  return (
    <div className="rounded-lg border p-4 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-xs text-muted-foreground">#{step.step_order}</span>
        <span className="rounded bg-muted px-2 py-0.5 text-xs">{step.step_type}</span>
        {step.failed_flag && <span className="text-xs text-destructive">failed</span>}
        {step.successful_flag && <span className="text-xs text-emerald-600">success</span>}
        <span className="text-xs text-muted-foreground">
          confidence {(step.extraction_confidence * 100).toFixed(0)}%
        </span>
        {canEdit && !editing && (
          <Button
            variant="ghost"
            size="icon"
            className="ml-auto h-6 w-6"
            onClick={() => setEditing(true)}
          >
            <Pencil className="h-3 w-3" />
          </Button>
        )}
      </div>

      {editing ? (
        <div className="mt-2 space-y-2">
          <Textarea
            rows={3}
            value={text}
            onChange={(e) => setText(e.target.value)}
            className="text-sm"
          />
          <Input
            placeholder="Observation (optional)"
            value={observation}
            onChange={(e) => setObservation(e.target.value)}
            className="text-sm"
          />
          <div className="flex gap-2">
            <Button size="sm" disabled={mut.isPending} onClick={() => mut.mutate()}>
              <Check className="mr-1 h-3 w-3" />
              {mut.isPending ? "Saving…" : "Save"}
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => { setEditing(false); setText(step.text); setObservation(step.observation ?? ""); }}
            >
              <X className="mr-1 h-3 w-3" />
              Cancel
            </Button>
          </div>
        </div>
      ) : (
        <>
          <p className="mt-2 whitespace-pre-wrap">{step.text}</p>
          {step.observation && (
            <p className="mt-2 text-muted-foreground italic">{step.observation}</p>
          )}
        </>
      )}
    </div>
  );
}

export default function EpisodeDetailPage() {
  const params = useParams<{ id: string }>();
  const episodeId = params.id;
  const qc = useQueryClient();
  const roles = useAuthStore((s) => s.roles);
  const showApprove = canApproveEpisode(roles);

  const { data: episode, isLoading, error } = useQuery({
    queryKey: ["episode", episodeId],
    queryFn: () => api.get<EpisodeDetail>(`/episodes/${episodeId}`),
    enabled: !!episodeId,
  });

  const approveMut = useMutation({
    mutationFn: () => api.post(`/episodes/${episodeId}/approve`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["episode", episodeId] });
      qc.invalidateQueries({ queryKey: ["episodes"] });
    },
  });

  if (!episodeId) return null;

  if (isLoading) {
    return (
      <DetailPageSkeleton actionSlots={2}>
        <DetailCardGridSkeleton count={2} />
        <div className="h-px w-full bg-border" />
        <div className="space-y-3">
          <Skeleton className="h-6 w-28" />
          <div className="space-y-3">
            <div className="rounded-lg border bg-card p-4 shadow-sm">
              <Skeleton className="h-4 w-3/4" />
              <Skeleton className="mt-3 h-16 w-full" />
            </div>
            <div className="rounded-lg border bg-card p-4 shadow-sm">
              <Skeleton className="h-4 w-3/4" />
              <Skeleton className="mt-3 h-16 w-full" />
            </div>
          </div>
        </div>
      </DetailPageSkeleton>
    );
  }

  if (error || !episode) {
    return (
      <div className="space-y-4">
        <PageHeader
          title="Episode"
          description="Not found."
          backHref="/episodes"
          backLabel="Episodes"
        />
        <p className="text-sm text-destructive">{String((error as Error)?.message || "Missing")}</p>
      </div>
    );
  }

  const steps = [...(episode.steps || [])].sort((a, b) => a.step_order - b.step_order);
  const evidenceItems = episode.evidence_items ?? [];
  const fallbackEvidenceIds = (episode.evidence_ids ?? []).filter(
    (eid) => !evidenceItems.some((item) => item.id === eid),
  );
  const evidenceCount =
    episode.evidence_count ?? evidenceItems.length + fallbackEvidenceIds.length;

  return (
    <div className="space-y-6">
      <PageHeader
        title={episode.title}
        description="Structured troubleshooting timeline with extracted steps."
        backHref="/episodes"
        backLabel="Episodes"
        actions={
          <div className="flex items-center gap-2">
            {showApprove && episode.reviewer_state !== "approved" && (
              <Button
                disabled={approveMut.isPending}
                onClick={() => approveMut.mutate()}
              >
                {approveMut.isPending ? (
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                ) : (
                  <CheckCircle2 className="mr-2 h-4 w-4" />
                )}
                Approve episode
              </Button>
            )}
          </div>
        }
      />

      <div className="flex flex-wrap gap-2">
        <StatusBadge status={episode.status} />
        <StatusBadge status={episode.reviewer_state} />
        <span className="text-xs text-muted-foreground self-center">
          Extraction {(episode.extraction_confidence * 100).toFixed(0)}%
        </span>
      </div>

      {/* A superseded episode is a draft reconstruction replaced by a
          later one, and it is usually the worse of the two -- the
          replaced ActiveMQ draft conflated two incidents and recorded no
          remediation. A status badge alone does not carry that, so a
          reader takes the stale narrative for the current one. */}
      {episode.reviewer_state === "superseded" && (
        <div className="flex items-start gap-2 rounded-lg border border-amber-500/40 bg-amber-500/10 p-3 text-sm">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-500" />
          <div>
            <p className="font-medium text-foreground">This reconstruction was superseded</p>
            <p className="text-xs text-muted-foreground">
              A later reconstruction replaced it as more of the thread arrived. It is
              kept for audit; the current narrative for this incident is another
              episode.
            </p>
          </div>
        </div>
      )}

      {/* The AI reviewer's assessment, when one exists. Advisory by design:
          the verdict names its reasons so the human can agree or override,
          and an auto-approval says so instead of impersonating a person. */}
      {episode.ai_review && (
        <div
          className={
            "flex items-start gap-2 rounded-lg border p-3 text-sm " +
            (episode.ai_review.verdict === "approve"
              ? "border-sky-500/40 bg-sky-500/10"
              : "border-amber-500/40 bg-amber-500/10")
          }
        >
          <div>
            <p className="font-medium">
              AI review: {episode.ai_review.auto_approved
                ? "auto-approved"
                : `${episode.ai_review.verdict} (${(episode.ai_review.confidence * 100).toFixed(0)}% confidence)`}
            </p>
            {(episode.ai_review.reasons ?? []).length > 0 && (
              <ul className="mt-1 list-disc pl-5 text-xs text-muted-foreground">
                {(episode.ai_review.reasons ?? []).map((reason, i) => (
                  <li key={i}>{reason}</li>
                ))}
              </ul>
            )}
            {(episode.ai_review.failed_floors ?? []).length > 0 && (
              <p className="mt-1 text-xs text-muted-foreground">
                Floors not met: {(episode.ai_review.failed_floors ?? []).join(", ")}
              </p>
            )}
          </div>
        </div>
      )}

      <div className="grid gap-4 md:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Summary</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3 text-sm">
            {episode.root_cause_summary && (
              <div>
                <p className="text-muted-foreground text-xs uppercase tracking-wide">Root cause</p>
                <p className="mt-1 whitespace-pre-wrap">{episode.root_cause_summary}</p>
              </div>
            )}
            {episode.final_outcome && (
              <div>
                <p className="text-muted-foreground text-xs uppercase tracking-wide">Outcome</p>
                <p className="mt-1 whitespace-pre-wrap">{episode.final_outcome}</p>
              </div>
            )}
            {!episode.root_cause_summary && !episode.final_outcome && (
              <p className="text-muted-foreground">No summary fields yet.</p>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Links</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            <div>
              <span className="text-muted-foreground">Domain</span>{" "}
              <span className="font-mono text-xs">{episode.domain_id ?? "—"}</span>
            </div>
            {episode.primary_case_ref && (
              <div>
                <span className="text-muted-foreground">Case ref</span>
                <p className="font-mono text-xs mt-1">{episode.primary_case_ref}</p>
              </div>
            )}
            <div>
              <p className="text-muted-foreground text-xs mb-1">
                Evidence ({evidenceCount})
              </p>
              {evidenceItems.length > 0 || fallbackEvidenceIds.length > 0 ? (
                <ul className="space-y-1 max-h-40 overflow-auto">
                  {evidenceItems.map((item) => (
                    <li key={item.id}>
                      <Link
                        href={`/evidence/${item.id}`}
                        className="text-xs text-primary hover:underline"
                      >
                        {item.title || item.id}
                      </Link>
                      <p className="text-[11px] text-muted-foreground">
                        {item.evidence_type} - {item.relevance_state}
                      </p>
                    </li>
                  ))}
                  {fallbackEvidenceIds.map((eid) => (
                    <li key={eid}>
                      <Link
                        href={`/evidence/${eid}`}
                        className="font-mono text-xs text-primary hover:underline"
                      >
                        {eid}
                      </Link>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-xs text-muted-foreground">
                  No evidence linked to this episode.
                </p>
              )}
            </div>
          </CardContent>
        </Card>
      </div>

      <Separator />

      <div className="space-y-3">
        <div>
          <h3 className="text-lg font-semibold">Timeline</h3>
          <p className="text-xs text-muted-foreground">
            What happened, in order. The procedure derived from this lives on the
            playbook.
          </p>
        </div>
        {steps.length === 0 ? (
          <p className="text-sm text-muted-foreground">No timeline was extracted for this episode.</p>
        ) : (
          <div className="space-y-3">
            {steps.map((s) => (
              <StepCard key={s.id} step={s} episodeId={episodeId} canEdit={showApprove} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
