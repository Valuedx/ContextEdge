"use client";

import { useState, useCallback } from "react";
import { useQuery } from "@tanstack/react-query";
import { graphApi } from "@/lib/graph-api";
import type { GraphNeighbor, GraphScope } from "@/lib/types/graph";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Loader2,
  GitBranch,
  Search,
  ArrowRight,
  ArrowLeft,
  ExternalLink,
} from "lucide-react";
import { nodeColors, NODE_TYPE_OPTIONS } from "./graph-constants";

export function GraphNeighbors({ scope }: { scope: GraphScope }) {
  const [nodeType, setNodeType] = useState("pattern");
  const [nodeId, setNodeId] = useState("");
  const [edgeTypeFilter, setEdgeTypeFilter] = useState("");
  const [maxDepth, setMaxDepth] = useState(1);
  const [queryParams, setQueryParams] = useState<{
    nodeType: string;
    nodeId: string;
    edgeType: string;
    depth: number;
  } | null>(null);

  const { data, isLoading, error, isFetching } = useQuery<GraphNeighbor[]>({
    queryKey: ["graph-neighbors", queryParams, scope.domainId, scope.asOf],
    queryFn: () =>
      graphApi.neighbors(
        queryParams!.nodeType,
        queryParams!.nodeId,
        queryParams!.edgeType,
        queryParams!.depth,
        scope,
      ),
    enabled: !!queryParams,
  });

  const handleSearch = useCallback(() => {
    const trimmed = nodeId.trim();
    if (!trimmed) return;
    setQueryParams({
      nodeType: nodeType,
      nodeId: trimmed,
      edgeType: edgeTypeFilter.trim(),
      depth: maxDepth,
    });
  }, [nodeType, nodeId, edgeTypeFilter, maxDepth]);

  const handleFollowNode = useCallback(
    (neighbor: GraphNeighbor) => {
      setNodeType(neighbor.node_type);
      setNodeId(neighbor.node_id);
      setQueryParams({
        nodeType: neighbor.node_type,
        nodeId: neighbor.node_id,
        edgeType: edgeTypeFilter.trim(),
        depth: maxDepth,
      });
    },
    [edgeTypeFilter, maxDepth],
  );

  const grouped = data
    ? data.reduce<Record<number, GraphNeighbor[]>>((acc, n) => {
        (acc[n.depth] ??= []).push(n);
        return acc;
      }, {})
    : {};

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Search className="h-4 w-4" /> BFS Neighbor Traversal
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex flex-wrap items-end gap-3">
            <div className="space-y-1">
              <label className="text-xs text-muted-foreground">Node Type</label>
              <select
                value={nodeType}
                onChange={(e) => setNodeType(e.target.value)}
                className="h-8 rounded-lg border border-white/15 bg-white/[0.06] px-2.5 text-sm outline-none backdrop-blur-md"
              >
                {NODE_TYPE_OPTIONS.map((t) => (
                  <option key={t} value={t}>
                    {t.replace(/_/g, " ")}
                  </option>
                ))}
              </select>
            </div>

            <div className="flex-1 min-w-[280px] space-y-1">
              <label className="text-xs text-muted-foreground">Node ID (UUID)</label>
              <Input
                placeholder="e.g. a1b2c3d4-e5f6-7890-abcd-ef1234567890"
                value={nodeId}
                onChange={(e) => setNodeId(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && handleSearch()}
              />
            </div>

            <div className="space-y-1">
              <label className="text-xs text-muted-foreground">Edge Type (optional)</label>
              <Input
                placeholder="e.g. belongs_to"
                value={edgeTypeFilter}
                onChange={(e) => setEdgeTypeFilter(e.target.value)}
                className="w-40"
              />
            </div>

            <div className="space-y-1">
              <label className="text-xs text-muted-foreground">Depth</label>
              <select
                value={maxDepth}
                onChange={(e) => setMaxDepth(Number(e.target.value))}
                className="h-8 rounded-lg border border-white/15 bg-white/[0.06] px-2.5 text-sm outline-none backdrop-blur-md"
              >
                <option value={1}>1 hop</option>
                <option value={2}>2 hops</option>
                <option value={3}>3 hops</option>
              </select>
            </div>

            <Button onClick={handleSearch} disabled={!nodeId.trim() || isFetching}>
              {isFetching ? (
                <Loader2 className="h-4 w-4 animate-spin mr-1" />
              ) : (
                <GitBranch className="h-4 w-4 mr-1" />
              )}
              Traverse
            </Button>
          </div>
        </CardContent>
      </Card>

      {isLoading && (
        <div className="space-y-3">
          {[1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-16 rounded-xl" />
          ))}
        </div>
      )}

      {error && (
        <div className="text-destructive p-4 border rounded-xl bg-destructive/10">
          {(error as Error).message || "Failed to load neighbors"}
        </div>
      )}

      {data && !isLoading && data.length === 0 && (
        <Card>
          <CardContent className="py-8">
            <div className="text-center space-y-3">
              <GitBranch className="h-12 w-12 text-slate-600 mx-auto" />
              <p className="text-slate-400 text-sm">No neighbors found for this node.</p>
            </div>
          </CardContent>
        </Card>
      )}

      {data && !isLoading && data.length > 0 && (
        <div className="space-y-4">
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            Found <Badge variant="secondary">{data.length} neighbors</Badge> across{" "}
            <Badge variant="secondary">{Object.keys(grouped).length} depth levels</Badge>
          </div>

          {Object.entries(grouped)
            .sort(([a], [b]) => Number(a) - Number(b))
            .map(([depth, neighbors]) => (
              <Card key={depth}>
                <CardHeader>
                  <CardTitle className="text-sm">
                    Depth {depth} — {neighbors.length} neighbor{neighbors.length !== 1 ? "s" : ""}
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  <div className="divide-y divide-white/5">
                    {neighbors.map((n, i) => {
                      const c = nodeColors[n.node_type];
                      return (
                        <div
                          key={`${n.node_type}:${n.node_id}:${i}`}
                          className="flex items-center gap-3 py-2.5 first:pt-0 last:pb-0"
                        >
                          <div className="flex items-center gap-1.5 text-xs text-muted-foreground w-20 shrink-0">
                            {n.direction === "outgoing" ? (
                              <ArrowRight className="h-3.5 w-3.5 text-emerald-400" />
                            ) : (
                              <ArrowLeft className="h-3.5 w-3.5 text-sky-400" />
                            )}
                            <span>{n.direction}</span>
                          </div>

                          <Badge variant="outline" className="shrink-0 text-xs">
                            {n.edge_type.replace(/_/g, " ")}
                          </Badge>

                          <div className="flex items-center gap-2 flex-1 min-w-0">
                            <div className={`h-2.5 w-2.5 rounded-sm shrink-0 ${c?.dot ?? "bg-slate-500"}`} />
                            <span className="text-sm font-medium truncate">
                              {n.node_type}
                            </span>
                            <span className="text-xs text-muted-foreground font-mono truncate">
                              {n.node_id}
                            </span>
                          </div>

                          <span className="text-xs tabular-nums text-muted-foreground shrink-0">
                            w={n.weight.toFixed(1)}
                          </span>

                          <Button
                            variant="ghost"
                            size="icon"
                            className="h-7 w-7 shrink-0"
                            title="Follow this node"
                            onClick={() => handleFollowNode(n)}
                          >
                            <ExternalLink className="h-3.5 w-3.5" />
                          </Button>
                        </div>
                      );
                    })}
                  </div>
                </CardContent>
              </Card>
            ))}
        </div>
      )}
    </div>
  );
}
