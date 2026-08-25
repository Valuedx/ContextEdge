"use client";

import { useQuery } from "@tanstack/react-query";
import { graphApi } from "@/lib/graph-api";
import type { GraphScope, GraphStatsResponse } from "@/lib/types/graph";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Layers, Network, ArrowRightLeft } from "lucide-react";
import { nodeColors, edgeColors } from "./graph-constants";

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
    <Card>
      <CardContent className="flex items-center gap-4 pt-1">
        <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-muted">
          <Icon className="h-5 w-5 text-primary" />
        </div>
        <div>
          <p className="text-2xl font-bold tabular-nums">{value.toLocaleString()}</p>
          <p className="text-xs text-muted-foreground">{label}</p>
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
  if (total === 0) return null;

  return (
    <div className="space-y-3">
      <div className="flex h-3 w-full overflow-hidden rounded-full bg-muted">
        {items.map(([name, count]) => {
          const pct = (count / total) * 100;
          const c = colorMap[name];
          return (
            <div
              key={name}
              className={`${c?.dot ?? "bg-slate-500"} transition-all`}
              style={{ width: `${pct}%` }}
              title={`${name}: ${count}`}
            />
          );
        })}
      </div>
      <div className="grid grid-cols-2 gap-x-6 gap-y-2 sm:grid-cols-3 lg:grid-cols-4">
        {items.map(([name, count]) => {
          const c = colorMap[name];
          return (
            <div key={name} className="flex items-center gap-2 text-sm">
              <div className={`h-2.5 w-2.5 shrink-0 rounded-sm ${c?.dot ?? "bg-slate-500"}`} />
              <span className="truncate text-foreground">{name.replace(/_/g, " ")}</span>
              <span className="ml-auto tabular-nums text-muted-foreground">{count}</span>
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
      <div className="text-destructive p-4 border rounded-lg bg-destructive/10">
        Failed to load graph statistics
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
        <CardHeader>
          <CardTitle>Node Type Distribution ({totalNodes})</CardTitle>
        </CardHeader>
        <CardContent>
          <DistributionBar items={nodeEntries} colorMap={nodeColors} />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Edge Type Distribution ({data.total_edges})</CardTitle>
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
