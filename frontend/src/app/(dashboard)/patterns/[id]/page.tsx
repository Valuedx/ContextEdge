"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";
import { PageHeader } from "@/components/common/page-header";
import {
  DetailPageSkeleton,
  DetailStatCardsSkeleton,
  DetailWideCardSkeleton,
} from "@/components/common/detail-page-skeleton";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";
import { usePagination } from "@/lib/hooks/use-pagination";
import type { Pattern, PatternEvidenceLink } from "@/lib/types";

import { PatternGraph } from "@/components/patterns/pattern-graph";
import { AlertCircle, Zap, Shield, Bug, Lightbulb, StepForward, Activity, Link2, Plus, Trash2, CheckCircle2, Sparkles, Loader2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { PaginationControls } from "@/components/common/pagination-controls";

const LINK_TYPES = ["supports", "contradicts", "derives", "anchors", "illustrates"];

function AddLinkDialog({ patternId, onClose }: { patternId: string; onClose: () => void }) {
  const qc = useQueryClient();
  const [evidenceId, setEvidenceId] = useState("");
  const [linkType, setLinkType] = useState("supports");
  const [weight, setWeight] = useState("1.0");

  const mut = useMutation({
    mutationFn: () =>
      api.post(`/patterns/${patternId}/evidence-links`, {
        evidence_id: evidenceId.trim() || undefined,
        link_type: linkType,
        weight: parseFloat(weight) || 1.0,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["pattern-evidence-links", patternId] });
      toast.success("Evidence link added");
      onClose();
    },
    onError: (err: Error) => toast.error(err.message || "Add failed"),
  });

  return (
    <DialogContent>
      <DialogHeader>
        <DialogTitle>Add evidence link</DialogTitle>
      </DialogHeader>
      <div className="space-y-4 text-sm">
        <div>
          <Label htmlFor="ev-id">Evidence ID</Label>
          <Input
            id="ev-id"
            className="mt-1 font-mono text-xs"
            value={evidenceId}
            onChange={(e) => setEvidenceId(e.target.value)}
            placeholder="UUID"
          />
        </div>
        <div>
          <Label>Link type</Label>
          <select
            className="mt-1 w-full rounded-md border bg-background px-3 py-2 text-sm"
            value={linkType}
            onChange={(e) => setLinkType(e.target.value)}
          >
            {LINK_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
        </div>
        <div>
          <Label htmlFor="lk-weight">Weight</Label>
          <Input
            id="lk-weight"
            type="number"
            min="0"
            step="0.1"
            className="mt-1"
            value={weight}
            onChange={(e) => setWeight(e.target.value)}
          />
        </div>
      </div>
      <DialogFooter>
        <Button variant="outline" onClick={onClose}>Cancel</Button>
        <Button disabled={mut.isPending || !evidenceId.trim()} onClick={() => mut.mutate()}>
          {mut.isPending ? "Adding..." : "Add"}
        </Button>
      </DialogFooter>
    </DialogContent>
  );
}

export default function PatternDetailPage() {
  const params = useParams<{ id: string }>();
  const patternId = params.id;
  const qc = useQueryClient();
  const [addLinkOpen, setAddLinkOpen] = useState(false);
  const evidencePg = usePagination(10);

  const { data: pattern, isLoading, error } = useQuery({
    queryKey: ["pattern", patternId],
    queryFn: () => api.get<Pattern>(`/patterns/${patternId}`),
    enabled: !!patternId,
  });

  const { data: evidenceLinks = [], isFetching: evidenceLinksFetching } = useQuery<PatternEvidenceLink[]>({
    queryKey: ["pattern-evidence-links", patternId, evidencePg.page],
    queryFn: () => api.get<PatternEvidenceLink[]>(`/patterns/${patternId}/evidence-links`, evidencePg.params),
    enabled: !!patternId,
  });

  const delLink = useMutation({
    mutationFn: (linkId: string) => api.delete(`/patterns/${patternId}/evidence-links/${linkId}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["pattern-evidence-links", patternId] });
      if (evidenceLinks.length === 1 && evidencePg.page > 0) {
        evidencePg.prevPage();
      }
    },
    onError: (err: Error) => toast.error(err.message || "Delete failed"),
  });

  const approveMut = useMutation({
    mutationFn: () => api.post<Pattern>(`/patterns/${patternId}/approve`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["pattern", patternId] });
      toast.success("Pattern approved and activated");
    },
    onError: (err: Error) => toast.error(err.message || "Approval failed"),
  });

  const generatePbMut = useMutation({
    mutationFn: () => api.post<{ id: string }>(`/playbooks/generate`, { pattern_id: patternId }),
    onSuccess: (res) => {
      toast.success("Playbook candidate generated!");
      if (res?.id) {
        window.location.href = `/playbooks/${res.id}`;
      }
    },
    onError: (err: Error) => toast.error(err.message || "Playbook generation failed"),
  });

  if (!patternId) return null;

  if (isLoading) {
    return (
      <DetailPageSkeleton>
        <DetailStatCardsSkeleton count={3} />
        <DetailWideCardSkeleton lines={4} />
        <DetailWideCardSkeleton lines={8} />
      </DetailPageSkeleton>
    );
  }

  if (error || !pattern) {
    return (
      <div className="space-y-4">
        <PageHeader
          title="Pattern"
          description="Not found."
          backHref="/patterns"
          backLabel="Patterns"
        />
        <p className="text-sm text-destructive">{String((error as Error)?.message || "Missing")}</p>
      </div>
    );
  }

  return (
    <div className="space-y-6 pb-12">
      <PageHeader
        title={pattern.title}
        description={`${pattern.pattern_type.replace("_", " ")} · ${pattern.episode_count} episodes linked`}
        backHref="/patterns"
        backLabel="Patterns"
        actions={
          <div className="flex items-center gap-2">
            <Button
              variant={pattern.active_flag ? "outline" : "default"}
              disabled={approveMut.isPending || pattern.active_flag}
              onClick={() => approveMut.mutate()}
              className={pattern.active_flag ? "border-emerald-500/50 text-emerald-400" : "bg-emerald-600 hover:bg-emerald-700"}
            >
              {approveMut.isPending ? (
                <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
              ) : (
                <CheckCircle2 className="mr-1.5 h-4 w-4" />
              )}
              {pattern.active_flag ? "Approved" : "Approve Pattern"}
            </Button>

            <Button
              variant="default"
              disabled={generatePbMut.isPending}
              onClick={() => generatePbMut.mutate()}
            >
              {generatePbMut.isPending ? (
                <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
              ) : (
                <Sparkles className="mr-1.5 h-4 w-4" />
              )}
              Generate Playbook
            </Button>

          </div>
        }
      />

      <div className="grid gap-4 md:grid-cols-4">
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              <Activity className="h-3 w-3 text-primary" /> Confidence
            </CardTitle>
          </CardHeader>
          <CardContent className="text-2xl font-bold text-foreground">
            {(pattern.confidence * 100).toFixed(0)}%
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              <AlertCircle className="h-3 w-3 text-rose-400" /> Contradiction
            </CardTitle>
          </CardHeader>
          <CardContent className="text-2xl font-bold text-foreground">
            {(pattern.contradiction_score * 100).toFixed(0)}%
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              <Zap className="h-3 w-3 text-amber-400" /> Freshness
            </CardTitle>
          </CardHeader>
          <CardContent className="text-2xl font-bold text-foreground">
            {(pattern.freshness_score * 100).toFixed(0)}%
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              <Shield className="h-3 w-3 text-emerald-400" /> Episode Count
            </CardTitle>
          </CardHeader>
          <CardContent className="text-2xl font-bold text-foreground">
            {pattern.episode_count}
          </CardContent>
        </Card>
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,2fr)_minmax(18rem,1fr)] lg:items-start">
        <div className="min-w-0 space-y-6">
          <Card>
            <CardHeader>
              <CardTitle className="text-base font-semibold text-foreground">Knowledge Graph Visualization</CardTitle>
            </CardHeader>
            <CardContent className="p-0">
              <PatternGraph patternId={patternId} />
            </CardContent>
          </Card>

          {pattern.resolution_steps && pattern.resolution_steps.length > 0 && (
            <Card className="border-l-4 border-l-primary">
              <CardHeader>
                <CardTitle className="flex items-center gap-2 text-base font-semibold text-foreground">
                   <StepForward className="h-4 w-4 text-primary" /> Synthesized Resolution Path
                </CardTitle>
              </CardHeader>
              <CardContent>
                <div className="space-y-4">
                   {pattern.resolution_steps.map((step, i) => (
                     <div key={i} className="flex gap-4">
                        <div className="flex h-6 w-6 flex-none items-center justify-center rounded-full border bg-muted text-xs font-bold text-foreground">
                           {i + 1}
                        </div>
                        <p className="pt-0.5 text-sm leading-relaxed text-foreground">{step}</p>
                     </div>
                   ))}
                </div>
              </CardContent>
            </Card>
          )}

          <Card>
            <CardHeader className="flex flex-col gap-3 border-b sm:flex-row sm:items-center sm:justify-between">
              <div>
                <CardTitle className="flex items-center gap-2 text-base font-semibold text-foreground">
                  <Link2 className="h-4 w-4 text-primary" /> Evidence Links
                </CardTitle>
                <p className="mt-1 text-xs text-muted-foreground">
                  Page {evidencePg.page + 1} - {evidencePg.pageSize} links per request
                </p>
              </div>
              <Button size="sm" onClick={() => setAddLinkOpen(true)}>
                <Plus className="mr-1.5 h-3.5 w-3.5" />
                Add link
              </Button>
            </CardHeader>
            <CardContent className="p-0">
              {evidenceLinksFetching && evidenceLinks.length === 0 ? (
                <div className="space-y-2 p-4">
                  {Array.from({ length: 3 }).map((_, i) => (
                    <div key={i} className="h-10 animate-pulse rounded-md bg-muted" />
                  ))}
                </div>
              ) : evidenceLinks.length === 0 ? (
                <p className="p-4 text-sm text-muted-foreground">No evidence links yet.</p>
              ) : (
                <>
                  <div className="divide-y">
                    {evidenceLinks.map((lk) => (
                      <div
                        key={lk.id}
                        className="grid items-center gap-3 px-4 py-3 text-sm sm:grid-cols-[auto_minmax(0,1fr)_auto_auto]"
                      >
                        <Badge variant="outline" className="w-fit text-xs">
                          {lk.link_type}
                        </Badge>
                        <span className="min-w-0 truncate font-mono text-xs text-muted-foreground">
                          {lk.evidence_id ?? lk.episode_id ?? "-"}
                        </span>
                        <span className="text-xs text-muted-foreground">w={lk.weight.toFixed(1)}</span>
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-7 w-7 justify-self-end"
                          disabled={delLink.isPending}
                          onClick={() => delLink.mutate(lk.id)}
                        >
                          <Trash2 className="h-3.5 w-3.5 text-destructive" />
                        </Button>
                      </div>
                    ))}
                  </div>
                  <div className="border-t px-4 pb-3">
                    <PaginationControls
                      page={evidencePg.page}
                      pageSize={evidencePg.pageSize}
                      count={evidenceLinks.length}
                      onPrev={evidencePg.prevPage}
                      onNext={evidencePg.nextPage}
                    />
                  </div>
                </>
              )}
            </CardContent>
          </Card>

          {pattern.observed_errors && pattern.observed_errors.length > 0 && (
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="flex items-center gap-2 text-sm font-semibold text-foreground">
                  <Bug className="h-3 w-3 text-rose-500" /> Observed Errors
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-2">
                {pattern.observed_errors.map((error, i) => (
                  <div key={i} className="break-all rounded border border-rose-200 bg-rose-50 p-2 font-mono text-xs text-rose-700 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-300">
                    {error}
                  </div>
                ))}
              </CardContent>
            </Card>
          )}

          {pattern.root_causes && pattern.root_causes.length > 0 && (
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="flex items-center gap-2 text-sm font-semibold text-foreground">
                  <Lightbulb className="h-3 w-3 text-amber-500" /> Identified Root Causes
                </CardTitle>
              </CardHeader>
              <CardContent>
                 <ul className="space-y-2">
                  {pattern.root_causes.map((item, i) => (
                    <li key={i} className="flex items-start gap-2 text-xs text-muted-foreground">
                      <div className="mt-1 w-1.5 h-1.5 rounded-full bg-amber-500/50" /> {item}
                    </li>
                  ))}
                </ul>
              </CardContent>
            </Card>
          )}
        </div>

        <div className="min-w-0 space-y-6 lg:sticky lg:top-0 lg:max-h-[calc(100vh-8rem)] lg:overflow-y-auto lg:overscroll-contain lg:pr-1">
          <Card>
            <CardHeader>
              <CardTitle className="text-base font-semibold text-foreground">Pattern Summary</CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-sm leading-relaxed text-muted-foreground">{pattern.description}</p>
              
              {pattern.evidence_summary && (
                 <div className="mt-4 grid grid-cols-2 gap-2 border-t pt-4">
                    {Object.entries(pattern.evidence_summary)
                      .filter(([key, val]) => key !== "memory_promotion" && (typeof val === "string" || typeof val === "number"))
                      .map(([key, val]) => (
                        <div key={key} className="flex flex-col rounded border bg-muted p-2">
                           <span className="text-[10px] font-semibold uppercase tracking-tighter text-muted-foreground">{key.replace("_", " ")}</span>
                           <span className="text-lg font-bold text-foreground">{String(val)}</span>
                        </div>
                      ))}
                 </div>
              )}
            </CardContent>
          </Card>

          {pattern.trigger_conditions && pattern.trigger_conditions.length > 0 && (
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="flex items-center gap-2 text-sm font-semibold text-foreground">
                  <Zap className="h-3 w-3 text-amber-500" /> Trigger Conditions
                </CardTitle>
              </CardHeader>
              <CardContent>
                <ul className="space-y-2">
                  {pattern.trigger_conditions.map((item, i) => (
                    <li key={i} className="flex items-start gap-2 text-xs text-muted-foreground">
                      <div className="mt-1 w-1.5 h-1.5 rounded-full bg-amber-500/50" /> {item}
                    </li>
                  ))}
                </ul>
              </CardContent>
            </Card>
          )}

          {pattern.core_entities && pattern.core_entities.length > 0 && (
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="flex items-center gap-2 text-sm font-semibold text-foreground">
                  <Shield className="h-3 w-3 text-emerald-500" /> Core Entities
                </CardTitle>
              </CardHeader>
              <CardContent className="flex flex-wrap gap-2">
                {pattern.core_entities.map((entity, i) => (
                  <Badge key={i} variant="secondary" className="border-emerald-200 bg-emerald-50 text-emerald-700 hover:bg-emerald-100 dark:border-emerald-500/30 dark:bg-emerald-500/10 dark:text-emerald-300">
                    {entity}
                  </Badge>
                ))}
              </CardContent>
            </Card>
          )}
        </div>
      </div>


      <Dialog open={addLinkOpen} onOpenChange={(o) => { if (!o) setAddLinkOpen(false); }}>
        {addLinkOpen && patternId && (
          <AddLinkDialog patternId={patternId} onClose={() => setAddLinkOpen(false)} />
        )}
      </Dialog>
    </div>
  );
}
