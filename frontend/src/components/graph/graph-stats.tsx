"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowRightLeft, Layers, Network } from "lucide-react";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { graphApi } from "@/lib/graph-api";
import type { GraphScope, GraphStatsResponse } from "@/lib/types/graph";
import { edgeColors, nodeColors } from "./graph-constants";

function StatCard({
  label,
  value,
  icon: Icon,
}: {
  label: string;
  value: number;
  icon: React.ElementType;
}) {
  return (
    <Card className="group relative overflow-hidden transition-all duration-300 hover:-translate-y-0.5 hover:shadow-md">
      <div className="pointer-events-none absolute -right-6 -top-6 h-24 w-24 rounded-full bg-primary/5 blur-2xl transition-opacity group-hover:opacity-100" />
      <CardHeader className="relative flex flex-row items-center justify-between pb-2">
        <CardTitle className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
          {label}
        </CardTitle>
        <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary/10 text-primary">
          <Icon className="h-4 w-4" />
        </div>
      </CardHeader>
      <CardContent className="relative pt-0">
        <div className="text-2xl font-bold tracking-tight tabular-nums sm:text-3xl">
          {value.toLocaleString()}
        </div>
      </CardContent>
    </Card>
  );
}

function DistributionBar({
  items,
  colorMap,
}: {
  items: [string, number][];
  colorMap: Record<string, { dot: string }>;
}) {
  const total = items.reduce((sum, [, count]) => sum + count, 0);
  if (total === 0) {
    return (
      <p className="py-4 text-center text-xs text-muted-foreground">
        No records in this category.
      </p>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex h-2.5 w-full overflow-hidden rounded-full bg-muted/60 p-0.5 ring-1 ring-border/40">
        {items.map(([name, count]) => {
          const pct = (count / total) * 100;
          const c = colorMap[name];
          return (
            <div
              key={name}
              className={`${c?.dot ?? "bg-slate-500"} first:rounded-l-full last:rounded-r-full transition-all duration-300 hover:opacity-80`}
              style={{ width: `${pct}%` }}
              title={`${name.replace(/_/g, " ")}: ${count} (${pct.toFixed(1)}%)`}
            />
          );
        })}
      </div>
      <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-3 lg:grid-cols-4">
        {items.map(([name, count]) => {
          const c = colorMap[name];
          const pct = total > 0 ? ((count / total) * 100).toFixed(1) : "0";
          return (
            <div
              key={name}
              className="flex items-center gap-2 rounded-md border border-border/50 bg-card/60 px-3 py-2 text-xs transition-colors hover:bg-muted/30"
            >
              <div className={`h-2.5 w-2.5 shrink-0 rounded-full ${c?.dot ?? "bg-slate-500"}`} />
              <span className="truncate font-medium capitalize text-foreground">
                {name.replace(/_/g, " ")}
              </span>
              <span className="ml-auto shrink-0 font-mono text-muted-foreground">
                {count}{" "}
                <span className="text-[10px] text-muted-foreground/70">({pct}%)</span>
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export function GraphStats({ scope }: { scope: GraphScope }) {
  const { data, isLoading, error } = useQuery<GraphStatsResponse>({
    queryKey: ["graph-stats", scope.domainId, scope.asOf],
    queryFn: () => graphApi.stats(scope),
  });

  if (isLoading) {
    return (
      <div className="space-y-6">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          {[1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-24 rounded-lg" />
          ))}
        </div>
        <Skeleton className="h-48 rounded-lg" />
        <Skeleton className="h-48 rounded-lg" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="rounded-lg border border-destructive/30 bg-destructive/10 p-4 text-sm text-destructive">
        Failed to load graph statistics: {(error as Error).message || "Unknown error"}
      </div>
    );
  }

  if (!data) return null;

  const nodeEntries = Object.entries(data.node_type_counts).sort(([, a], [, b]) => b - a);
  const edgeEntries = Object.entries(data.edge_type_counts).sort(([, a], [, b]) => b - a);
  const totalNodes = nodeEntries.reduce((s, [, c]) => s + c, 0);

  const edgeColorMap: Record<string, { dot: string }> = {};
  for (const [k, v] of Object.entries(edgeColors)) {
    edgeColorMap[k] = { dot: colorToDot(v.stroke) };
  }

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <StatCard label="Total Edges" value={data.total_edges} icon={ArrowRightLeft} />
        <StatCard label="Node Types" value={nodeEntries.length} icon={Layers} />
        <StatCard label="Edge Types" value={edgeEntries.length} icon={Network} />
      </div>

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-sm font-semibold">
            Node Type Distribution ({totalNodes.toLocaleString()})
          </CardTitle>
        </CardHeader>
        <CardContent>
          <DistributionBar items={nodeEntries} colorMap={nodeColors} />
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-sm font-semibold">
            Edge Type Distribution ({data.total_edges.toLocaleString()})
          </CardTitle>
        </CardHeader>
        <CardContent>
          <DistributionBar items={edgeEntries} colorMap={edgeColorMap} />
        </CardContent>
      </Card>
    </div>
  );
}

function colorToDot(hex: string): string {
  const map: Record<string, string> = {
    "#818cf8": "bg-sky-400",
    "#fbbf24": "bg-amber-400",
    "#34d399": "bg-emerald-400",
    "#fb7185": "bg-rose-400",
    "#fb923c": "bg-amber-400",
    "#38bdf8": "bg-sky-400",
    "#a78bfa": "bg-violet-400",
    "#2dd4bf": "bg-teal-400",
    "#f43f5e": "bg-rose-500",
  };
  return map[hex] ?? "bg-slate-500";
}
