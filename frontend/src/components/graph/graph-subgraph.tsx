"use client";

import { useState, useEffect, useCallback } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";
import { Loader2, Network, Search, Info } from "lucide-react";
import {
  ReactFlow,
  useNodesState,
  useEdgesState,
  Background,
  Controls,
  Panel,
  Node,
  Edge,
  Position,
  MarkerType,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import dagre from "dagre";
import { getNodeClassName, edgeColors, nodeColors, NODE_TYPE_OPTIONS } from "./graph-constants";

interface SubgraphNode {
  type: string;
  id: string;
  title?: string | null;
}

interface SubgraphEdge {
  source: string;
  target: string;
  type: string;
  weight: number;
}

interface SubgraphResponse {
  nodes: SubgraphNode[];
  edges: SubgraphEdge[];
}

function layoutGraph(nodes: Node[], edges: Edge[], direction = "LR") {
  const g = new dagre.graphlib.Graph();
  g.setDefaultEdgeLabel(() => ({}));
  g.setGraph({ rankdir: direction, nodesep: 60, ranksep: 100 });

  nodes.forEach((node) => g.setNode(node.id, { width: 180, height: 50 }));
  edges.forEach((edge) => g.setEdge(edge.source, edge.target));

  dagre.layout(g);

  const isHorizontal = direction === "LR";
  return {
    nodes: nodes.map((node) => {
      const pos = g.node(node.id);
      return {
        ...node,
        targetPosition: isHorizontal ? Position.Left : Position.Top,
        sourcePosition: isHorizontal ? Position.Right : Position.Bottom,
        position: { x: pos.x - 90, y: pos.y - 25 },
      };
    }),
    edges,
  };
}

export function GraphSubgraph() {
  const [entityType, setEntityType] = useState("pattern");
  const [entityId, setEntityId] = useState("");
  const [maxDepth, setMaxDepth] = useState(1);
  const [queryParams, setQueryParams] = useState<{
    type: string;
    id: string;
    depth: number;
  } | null>(null);

  const [nodes, setNodes, onNodesChange] = useNodesState([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);

  const { data, isLoading, error, isFetching } = useQuery<SubgraphResponse>({
    queryKey: ["graph-subgraph", queryParams?.type, queryParams?.id, queryParams?.depth],
    queryFn: () =>
      api.get(`/graph/subgraph/${queryParams!.type}/${queryParams!.id}`, {
        max_depth: String(queryParams!.depth),
      }),
    enabled: !!queryParams,
  });

  useEffect(() => {
    if (!data || !data.nodes.length) {
      setNodes([]);
      setEdges([]);
      return;
    }

    const rawNodes: Node[] = data.nodes.map((n) => ({
      id: `${n.type}:${n.id}`,
      data: { label: n.title || n.type.replace(/_/g, " ").toUpperCase() },
      className: `px-4 py-2 border-2 rounded-lg text-sm transition-all hover:scale-105 ${getNodeClassName(n.type)}`,
      type: "default",
    }));

    const rawEdges: Edge[] = data.edges.map((e, i) => {
      const style = edgeColors[e.type] || { stroke: "#475569" };
      return {
        id: `e-${i}`,
        source: e.source,
        target: e.target,
        label: e.type.replace(/_/g, " "),
        labelStyle: { fill: "#94a3b8", fontSize: "10px", fontWeight: 500 },
        style: { stroke: style.stroke, strokeDasharray: style.dasharray },
        animated: true,
        markerEnd: { type: MarkerType.ArrowClosed, color: style.stroke },
      };
    });

    const { nodes: laid, edges: laidE } = layoutGraph(rawNodes, rawEdges);
    setNodes(laid);
    setEdges(laidE);
  }, [data, setNodes, setEdges]);

  const handleExplore = useCallback(() => {
    const trimmed = entityId.trim();
    if (!trimmed) return;
    setQueryParams({ type: entityType, id: trimmed, depth: maxDepth });
  }, [entityType, entityId, maxDepth]);

  const handleNodeClick = useCallback(
    (_: React.MouseEvent, node: Node) => {
      const [type, ...idParts] = node.id.split(":");
      const id = idParts.join(":");
      setEntityType(type);
      setEntityId(id);
      setQueryParams({ type, id, depth: maxDepth });
    },
    [maxDepth],
  );

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Search className="h-4 w-4" /> Explore Subgraph
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex flex-wrap items-end gap-3">
            <div className="space-y-1">
              <label className="text-xs text-muted-foreground">Entity Type</label>
              <select
                value={entityType}
                onChange={(e) => setEntityType(e.target.value)}
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
              <label className="text-xs text-muted-foreground">Entity ID (UUID)</label>
              <Input
                placeholder="e.g. a1b2c3d4-e5f6-7890-abcd-ef1234567890"
                value={entityId}
                onChange={(e) => setEntityId(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && handleExplore()}
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

            <Button onClick={handleExplore} disabled={!entityId.trim() || isFetching}>
              {isFetching ? (
                <Loader2 className="h-4 w-4 animate-spin mr-1" />
              ) : (
                <Network className="h-4 w-4 mr-1" />
              )}
              Explore
            </Button>
          </div>
        </CardContent>
      </Card>

      {isLoading && <Skeleton className="h-[500px] rounded-xl" />}

      {error && (
        <div className="text-destructive p-4 border rounded-xl bg-destructive/10">
          {(error as Error).message || "Failed to load subgraph"}
        </div>
      )}

      {data && !isLoading && (
        <div className="relative w-full h-[600px] border rounded-xl bg-[#020617] overflow-hidden">
          {data.nodes.length === 0 ? (
            <div className="absolute inset-0 flex items-center justify-center">
              <div className="text-center space-y-3">
                <Network className="h-12 w-12 text-slate-600 mx-auto" />
                <p className="text-slate-400 text-sm">No graph data found for this entity.</p>
              </div>
            </div>
          ) : (
            <ReactFlow
              nodes={nodes}
              edges={edges}
              onNodesChange={onNodesChange}
              onEdgesChange={onEdgesChange}
              onNodeClick={handleNodeClick}
              fitView
              colorMode="dark"
            >
              <Background color="#1e293b" gap={20} />
              <Controls showInteractive={false} className="bg-slate-900 border-slate-700 fill-slate-200" />

              <Panel position="top-left" className="bg-slate-900/90 border border-slate-700 p-3 rounded-lg text-xs space-y-2 backdrop-blur-sm shadow-xl">
                <div className="font-semibold text-slate-400 mb-1 flex items-center gap-1.5">
                  <Network className="h-3 w-3" /> Legend
                </div>
                <div className="grid grid-cols-2 gap-x-4 gap-y-1.5">
                  {Object.entries(nodeColors).map(([type, c]) => (
                    <div key={type} className="flex items-center gap-2">
                      <div className={`w-2.5 h-2.5 rounded-sm ${c.dot}`} />
                      {type.replace(/_/g, " ")}
                    </div>
                  ))}
                </div>
              </Panel>

              <Panel position="top-right" className="bg-slate-900/90 border border-slate-700 p-2.5 rounded-lg text-xs backdrop-blur-sm shadow-xl">
                <div className="flex items-center gap-1.5 text-slate-400">
                  <Info className="h-3 w-3" /> Click a node to re-center exploration
                </div>
              </Panel>

              <Panel position="bottom-left" className="bg-slate-900/90 border border-slate-700 p-2.5 rounded-lg text-xs backdrop-blur-sm shadow-xl">
                <div className="flex items-center gap-3 text-slate-400">
                  <Badge variant="secondary">{data.nodes.length} nodes</Badge>
                  <Badge variant="secondary">{data.edges.length} edges</Badge>
                </div>
              </Panel>
            </ReactFlow>
          )}
        </div>
      )}
    </div>
  );
}
