"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";
import { PageHeader } from "@/components/common/page-header";
import {
  DetailPageSkeleton,
  DetailStatCardsSkeleton,
  DetailWideCardSkeleton,
} from "@/components/common/detail-page-skeleton";
import { Button, buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";
import type { Pattern, PatternEvidenceLink } from "@/lib/types";

import { PatternGraph } from "@/components/patterns/pattern-graph";
import { AlertCircle, Zap, Shield, Bug, Lightbulb, StepForward, Activity, Link2, Plus, Trash2, CheckCircle2, Sparkles, Loader2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";

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
          {mut.isPending ? "Adding…" : "Add"}
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

  const { data: pattern, isLoading, error } = useQuery({
    queryKey: ["pattern", patternId],
    queryFn: () => api.get<Pattern>(`/patterns/${patternId}`),
    enabled: !!patternId,
  });

  const { data: evidenceLinks = [] } = useQuery<PatternEvidenceLink[]>({
    queryKey: ["pattern-evidence-links", patternId],
    queryFn: () => api.get(`/patterns/${patternId}/evidence-links`),
    enabled: !!patternId,
  });

  const delLink = useMutation({
    mutationFn: (linkId: string) => api.delete(`/patterns/${patternId}/evidence-links/${linkId}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["pattern-evidence-links", patternId] }),
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
        <PageHeader title="Pattern" description="Not found." />
        <p className="text-sm text-destructive">{String((error as Error)?.message || "Missing")}</p>
        <Link href="/patterns" className={cn(buttonVariants({ variant: "outline" }))}>
          Back to patterns
        </Link>
      </div>
    );
  }

  return (
    <div className="space-y-6 pb-12">
      <PageHeader
        title={pattern.title}
        description={`${pattern.pattern_type.replace("_", " ")} · ${pattern.episode_count} episodes linked`}
        actions={
          <div className="flex gap-2">
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
              className="bg-gradient-to-r from-indigo-600 to-purple-600 hover:from-indigo-700 hover:to-purple-700"
            >
              {generatePbMut.isPending ? (
                <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
              ) : (
                <Sparkles className="mr-1.5 h-4 w-4" />
              )}
              Generate Playbook
            </Button>

            <Link href="/patterns" className={cn(buttonVariants({ variant: "outline" }))}>
              All patterns
            </Link>
          </div>
        }
      />

      <div className="grid gap-4 md:grid-cols-4">
        <Card className="bg-slate-900/40 border-slate-800">
          <CardHeader className="pb-2">
            <CardTitle className="text-xs font-semibold text-slate-400 uppercase tracking-wider flex items-center gap-2">
              <Activity className="h-3 w-3 text-indigo-400" /> Confidence
            </CardTitle>
          </CardHeader>
          <CardContent className="text-2xl font-bold text-slate-100">
            {(pattern.confidence * 100).toFixed(0)}%
          </CardContent>
        </Card>
        <Card className="bg-slate-900/40 border-slate-800">
          <CardHeader className="pb-2">
            <CardTitle className="text-xs font-semibold text-slate-400 uppercase tracking-wider flex items-center gap-2">
              <AlertCircle className="h-3 w-3 text-rose-400" /> Contradiction
            </CardTitle>
          </CardHeader>
          <CardContent className="text-2xl font-bold text-slate-100">
            {(pattern.contradiction_score * 100).toFixed(0)}%
          </CardContent>
        </Card>
        <Card className="bg-slate-900/40 border-slate-800">
          <CardHeader className="pb-2">
            <CardTitle className="text-xs font-semibold text-slate-400 uppercase tracking-wider flex items-center gap-2">
              <Zap className="h-3 w-3 text-amber-400" /> Freshness
            </CardTitle>
          </CardHeader>
          <CardContent className="text-2xl font-bold text-slate-100">
            {(pattern.freshness_score * 100).toFixed(0)}%
          </CardContent>
        </Card>
        <Card className="bg-slate-900/40 border-slate-800">
          <CardHeader className="pb-2">
            <CardTitle className="text-xs font-semibold text-slate-400 uppercase tracking-wider flex items-center gap-2">
              <Shield className="h-3 w-3 text-emerald-400" /> Episode Count
            </CardTitle>
          </CardHeader>
          <CardContent className="text-2xl font-bold text-slate-100">
            {pattern.episode_count}
          </CardContent>
        </Card>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="lg:col-span-2 space-y-6">
          <Card className="bg-slate-900/40 border-slate-800">
            <CardHeader>
              <CardTitle className="text-base font-semibold text-slate-200">Knowledge Graph Visualization</CardTitle>
            </CardHeader>
            <CardContent className="p-0">
              <PatternGraph patternId={patternId} />
            </CardContent>
          </Card>

          {pattern.resolution_steps && pattern.resolution_steps.length > 0 && (
            <Card className="bg-slate-900/40 border-slate-800 border-l-4 border-l-indigo-500">
              <CardHeader>
                <CardTitle className="text-base font-semibold text-slate-200 flex items-center gap-2">
                   <StepForward className="h-4 w-4 text-indigo-400" /> Synthesized Resolution Path
                </CardTitle>
              </CardHeader>
              <CardContent>
                <div className="space-y-4">
                   {pattern.resolution_steps.map((step, i) => (
                     <div key={i} className="flex gap-4">
                        <div className="flex-none w-6 h-6 rounded-full bg-indigo-500/20 text-indigo-400 flex items-center justify-center text-xs font-bold border border-indigo-500/30">
                           {i + 1}
                        </div>
                        <p className="text-sm text-slate-300 leading-relaxed pt-0.5">{step}</p>
                     </div>
                   ))}
                </div>
              </CardContent>
            </Card>
          )}
        </div>

        <div className="space-y-6">
          <Card className="bg-slate-900/40 border-slate-800">
            <CardHeader>
              <CardTitle className="text-base font-semibold text-slate-200">Pattern Summary</CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-sm text-slate-400 leading-relaxed">{pattern.description}</p>
              
              {pattern.evidence_summary && (
                 <div className="mt-4 pt-4 border-t border-slate-800 grid grid-cols-2 gap-2">
                    {Object.entries(pattern.evidence_summary)
                      .filter(([key, val]) => key !== "memory_promotion" && (typeof val === "string" || typeof val === "number"))
                      .map(([key, val]) => (
                        <div key={key} className="bg-slate-950/50 p-2 rounded border border-slate-800/50 flex flex-col">
                           <span className="text-[10px] text-slate-500 uppercase tracking-tighter font-semibold">{key.replace("_", " ")}</span>
                           <span className="text-lg font-bold text-slate-300">{String(val)}</span>
                        </div>
                      ))}
                 </div>
              )}
            </CardContent>
          </Card>

          {pattern.trigger_conditions && pattern.trigger_conditions.length > 0 && (
            <Card className="bg-slate-900/40 border-slate-800">
              <CardHeader className="pb-2">
                <CardTitle className="text-sm font-semibold text-slate-200 flex items-center gap-2">
                  <Zap className="h-3 w-3 text-amber-500" /> Trigger Conditions
                </CardTitle>
              </CardHeader>
              <CardContent>
                <ul className="space-y-2">
                  {pattern.trigger_conditions.map((item, i) => (
                    <li key={i} className="text-xs text-slate-400 flex items-start gap-2">
                      <div className="mt-1 w-1.5 h-1.5 rounded-full bg-amber-500/50" /> {item}
                    </li>
                  ))}
                </ul>
              </CardContent>
            </Card>
          )}

          {pattern.core_entities && pattern.core_entities.length > 0 && (
            <Card className="bg-slate-900/40 border-slate-800">
              <CardHeader className="pb-2">
                <CardTitle className="text-sm font-semibold text-slate-200 flex items-center gap-2">
                  <Shield className="h-3 w-3 text-emerald-500" /> Core Entities
                </CardTitle>
              </CardHeader>
              <CardContent className="flex flex-wrap gap-2">
                {pattern.core_entities.map((entity, i) => (
                  <Badge key={i} variant="secondary" className="bg-emerald-500/10 text-emerald-400 hover:bg-emerald-500/20 border-emerald-500/20">
                    {entity}
                  </Badge>
                ))}
              </CardContent>
            </Card>
          )}

          {pattern.observed_errors && pattern.observed_errors.length > 0 && (
            <Card className="bg-slate-900/40 border-slate-800">
              <CardHeader className="pb-2">
                <CardTitle className="text-sm font-semibold text-slate-200 flex items-center gap-2">
                  <Bug className="h-3 w-3 text-rose-500" /> Observed Errors
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-2">
                {pattern.observed_errors.map((error, i) => (
                  <div key={i} className="text-xs font-mono bg-rose-500/5 border border-rose-500/20 p-2 rounded text-rose-300 break-all">
                    {error}
                  </div>
                ))}
              </CardContent>
            </Card>
          )}

          {pattern.root_causes && pattern.root_causes.length > 0 && (
            <Card className="bg-slate-900/40 border-slate-800">
              <CardHeader className="pb-2">
                <CardTitle className="text-sm font-semibold text-slate-200 flex items-center gap-2">
                  <Lightbulb className="h-3 w-3 text-orange-500" /> Identified Root Causes
                </CardTitle>
              </CardHeader>
              <CardContent>
                 <ul className="space-y-2">
                  {pattern.root_causes.map((item, i) => (
                    <li key={i} className="text-xs text-slate-400 flex items-start gap-2">
                      <div className="mt-1 w-1.5 h-1.5 rounded-full bg-orange-500/50" /> {item}
                    </li>
                  ))}
                </ul>
              </CardContent>
            </Card>
          )}
        </div>
      </div>

      <Separator />

      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <h3 className="text-lg font-semibold flex items-center gap-2">
            <Link2 className="h-4 w-4" /> Evidence links
          </h3>
          <Button size="sm" onClick={() => setAddLinkOpen(true)}>
            <Plus className="mr-1.5 h-3.5 w-3.5" />
            Add link
          </Button>
        </div>
        {evidenceLinks.length === 0 ? (
          <p className="text-sm text-muted-foreground">No evidence links yet.</p>
        ) : (
          <div className="space-y-2">
            {evidenceLinks.map((lk) => (
              <div key={lk.id} className="flex items-center justify-between rounded-md border px-3 py-2 text-sm">
                <div className="flex items-center gap-3 min-w-0">
                  <Badge variant="outline" className="text-xs shrink-0">{lk.link_type}</Badge>
                  <span className="font-mono text-xs truncate text-muted-foreground">
                    {lk.evidence_id ?? lk.episode_id ?? "—"}
                  </span>
                  <span className="text-xs text-muted-foreground shrink-0">w={lk.weight.toFixed(1)}</span>
                </div>
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-7 w-7 shrink-0"
                  disabled={delLink.isPending}
                  onClick={() => delLink.mutate(lk.id)}
                >
                  <Trash2 className="h-3.5 w-3.5 text-destructive" />
                </Button>
              </div>
            ))}
          </div>
        )}
      </div>

      <Dialog open={addLinkOpen} onOpenChange={(o) => { if (!o) setAddLinkOpen(false); }}>
        {addLinkOpen && patternId && (
          <AddLinkDialog patternId={patternId} onClose={() => setAddLinkOpen(false)} />
        )}
      </Dialog>
    </div>
  );
}
