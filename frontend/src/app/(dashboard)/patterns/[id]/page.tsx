"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useParams } from "next/navigation";
import { PageHeader } from "@/components/common/page-header";
import {
  DetailPageSkeleton,
  DetailStatCardsSkeleton,
  DetailWideCardSkeleton,
} from "@/components/common/detail-page-skeleton";
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";
import type { Pattern, PatternSubgraph } from "@/lib/types";

import { DetailsList } from "@/components/common/details-list";
import { PatternGraph } from "@/components/patterns/pattern-graph";
import { AlertCircle, Zap, Shield, Bug, Lightbulb, StepForward, Activity } from "lucide-react";
import { Badge } from "@/components/ui/badge";

export default function PatternDetailPage() {
  const params = useParams<{ id: string }>();
  const patternId = params.id;

  const { data: pattern, isLoading, error } = useQuery({
    queryKey: ["pattern", patternId],
    queryFn: () => api.get<Pattern>(`/patterns/${patternId}`),
    enabled: !!patternId,
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
            <Link href="/patterns" className={cn(buttonVariants({ variant: "outline" }))}>
              All patterns
            </Link>
            <Link href="/playbooks" className={cn(buttonVariants({ variant: "default" }))}>
              View Playbooks
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
                    {Object.entries(pattern.evidence_summary).map(([key, val]) => (
                      <div key={key} className="bg-slate-950/50 p-2 rounded border border-slate-800/50 flex flex-col">
                         <span className="text-[10px] text-slate-500 uppercase tracking-tighter font-semibold">{key.replace("_", " ")}</span>
                         <span className="text-lg font-bold text-slate-300">{val}</span>
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
    </div>
  );
}
