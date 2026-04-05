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

export default function PatternDetailPage() {
  const params = useParams<{ id: string }>();
  const patternId = params.id;

  const { data: pattern, isLoading, error } = useQuery({
    queryKey: ["pattern", patternId],
    queryFn: () => api.get<Pattern>(`/patterns/${patternId}`),
    enabled: !!patternId,
  });

  const { data: graph } = useQuery({
    queryKey: ["pattern-graph", patternId],
    queryFn: () => api.get<PatternSubgraph>(`/patterns/${patternId}/graph`),
    enabled: !!patternId && !!pattern,
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
    <div className="space-y-6">
      <PageHeader
        title={pattern.title}
        description={`${pattern.pattern_type} · ${pattern.episode_count} episodes`}
        actions={
          <Link href="/patterns" className={cn(buttonVariants({ variant: "outline" }))}>
            All patterns
          </Link>
        }
      />

      <div className="grid gap-4 md:grid-cols-3">
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">Confidence</CardTitle>
          </CardHeader>
          <CardContent className="text-2xl font-semibold">
            {(pattern.confidence * 100).toFixed(0)}%
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">Contradiction</CardTitle>
          </CardHeader>
          <CardContent className="text-2xl font-semibold">
            {(pattern.contradiction_score * 100).toFixed(0)}%
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">Freshness</CardTitle>
          </CardHeader>
          <CardContent className="text-2xl font-semibold">
            {(pattern.freshness_score * 100).toFixed(0)}%
          </CardContent>
        </Card>
      </div>

      {pattern.description && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Description</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-sm whitespace-pre-wrap">{pattern.description}</p>
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Context graph (subgraph)</CardTitle>
        </CardHeader>
        <CardContent>
          {graph ? (
            <pre className="max-h-96 overflow-auto rounded-md bg-muted p-3 text-xs">
              {JSON.stringify(graph, null, 2)}
            </pre>
          ) : (
            <p className="text-sm text-muted-foreground">No graph edges linked to this pattern yet.</p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
