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
  ArrowLeft,
  ArrowRight,
  ChevronDown,
  ExternalLink,
  GitBranch,
  Loader2,
  Search,
} from "lucide-react";
import { nodeColors, NODE_TYPE_OPTIONS } from "./graph-constants";
import {
  GraphNodePicker,
  loadGraphNodeOptions,
  type GraphNodeOption,
} from "./graph-node-picker";

function fallbackNodeLabel(nodeType: string, nodeId: string): string {
  return `${nodeType.replace(/_/g, " ")} ${nodeId.slice(0, 8)}`;
}

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

  const neighborTypes = Array.from(new Set((data || []).map((item) => item.node_type))).sort();
  const { data: labelMap = new Map<string, GraphNodeOption>() } = useQuery<
    Map<string, GraphNodeOption>
  >({
    queryKey: ["graph-neighbor-labels", neighborTypes.join("|")],
    queryFn: async () => {
      const loaded = await Promise.allSettled(
        neighborTypes.map(async (type) => ({
          type,
          options: await loadGraphNodeOptions(type),
        })),
      );
      const next = new Map<string, GraphNodeOption>();
      for (const result of loaded) {
        if (result.status !== "fulfilled") continue;
        for (const option of result.value.options) {
          next.set(`${result.value.type}:${option.id}`, option);
        }
      }
      return next;
    },
    enabled: neighborTypes.length > 0,
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
      <Card size="sm">
        <CardHeader className="pb-0">
          <CardTitle className="flex items-center gap-2">
            <Search className="h-4 w-4" /> BFS Neighbor Traversal
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex flex-wrap items-end gap-3">
            <GraphNodePicker
              nodeType={nodeType}
              nodeId={nodeId}
              nodeTypes={NODE_TYPE_OPTIONS}
              onNodeTypeChange={setNodeType}
              onNodeIdChange={setNodeId}
            />

            <div className="flex flex-col gap-1">
              <label className="text-xs font-medium text-muted-foreground">Edge Type (optional)</label>
              <Input
                placeholder="e.g. belongs_to"
                value={edgeTypeFilter}
                onChange={(e) => setEdgeTypeFilter(e.target.value)}
                className="h-8 w-36 text-xs sm:w-44"
              />
            </div>

            <div className="flex flex-col gap-1">
              <label className="text-xs font-medium text-muted-foreground">Depth</label>
              <div className="relative">
                <select
                  value={maxDepth}
                  onChange={(e) => setMaxDepth(Number(e.target.value))}
                  className="h-8 appearance-none rounded-md border border-input bg-background pl-2.5 pr-8 text-xs font-medium outline-none transition-colors hover:border-slate-400 focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/20"
                >
                  <option value={1}>1 hop</option>
                  <option value={2}>2 hops</option>
                  <option value={3}>3 hops</option>
                </select>
                <ChevronDown className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 size-3.5 text-muted-foreground opacity-60" />
              </div>
            </div>

            <Button size="sm" className="h-8" onClick={handleSearch} disabled={!nodeId.trim() || isFetching}>
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
            <Skeleton key={i} className="h-16 rounded-lg" />
          ))}
        </div>
      )}

      {error && (
        <div className="text-destructive p-4 border rounded-lg bg-destructive/10">
          {(error as Error).message || "Failed to load neighbors"}
        </div>
      )}

      {data && !isLoading && data.length === 0 && (
        <Card>
          <CardContent className="py-6">
            <div className="text-center space-y-3">
              <GitBranch className="h-12 w-12 text-muted-foreground mx-auto" />
              <p className="text-muted-foreground text-sm">No neighbors found for this node.</p>
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
                  <div className="divide-y">
                    {neighbors.map((n, i) => {
                      const c = nodeColors[n.node_type];
                      const option = labelMap.get(`${n.node_type}:${n.node_id}`);
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
                              {option?.label || fallbackNodeLabel(n.node_type, n.node_id)}
                            </span>
                            <span className="text-xs text-muted-foreground truncate">
                              {option?.meta || n.node_type.replace(/_/g, " ")}
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
