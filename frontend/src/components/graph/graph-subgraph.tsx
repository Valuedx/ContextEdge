"use client";

import { useState, useEffect, useCallback } from "react";
import { useQuery } from "@tanstack/react-query";
import { graphApi } from "@/lib/graph-api";
import type { GraphScope, GraphSubgraphResponse } from "@/lib/types/graph";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";
import { Loader2, Network, Search, Info } from "lucide-react";
import {
  ReactFlow,
  ReactFlowProvider,
  useNodesState,
  useEdgesState,
  useReactFlow,
  Background,
  Controls,
  Panel,
  Node,
  Edge,
  MarkerType,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { getNodeClassName, edgeColors, nodeColors, NODE_TYPE_OPTIONS } from "./graph-constants";
import { layoutGraph } from "./graph-layout";
import { GraphNodePicker } from "./graph-node-picker";

// Fresh dagre instance per call — avoids stale graph accumulation
// ── Inner canvas — must live inside ReactFlowProvider ────────────────────────

function FlowCanvas({
  nodes,
  edges,
  onNodesChange,
  onEdgesChange,
  onNodeClick,
  nodeCount,
  edgeCount,
}: {
  nodes: Node[];
  edges: Edge[];
  onNodesChange: ReturnType<typeof useNodesState>[2];
  onEdgesChange: ReturnType<typeof useEdgesState>[2];
  onNodeClick: (event: React.MouseEvent, node: Node) => void;
  nodeCount: number;
  edgeCount: number;
}) {
  const { fitView } = useReactFlow();

  useEffect(() => {
    if (nodes.length > 0) {
      requestAnimationFrame(() =>
        fitView({ padding: 0.15, duration: 350, maxZoom: 1.2 })
      );
    }
  }, [nodes, fitView]);

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      onNodesChange={onNodesChange}
      onEdgesChange={onEdgesChange}
      onNodeClick={onNodeClick}
      fitView
      colorMode="dark"
    >
      <Background color="#1e293b" gap={20} />
      <Controls
        showInteractive={false}
        className="bg-slate-900 border-slate-700 fill-slate-200"
      />

      <Panel
        position="top-left"
        className="bg-slate-900/90 border border-slate-700 p-3 rounded-lg text-xs space-y-2 backdrop-blur-sm shadow-xl"
      >
        <div className="font-semibold text-slate-400 mb-1 flex items-center gap-1.5">
          <Network className="h-3 w-3" /> Legend
        </div>
        <div className="grid grid-cols-2 gap-x-4 gap-y-1.5">
          {Object.entries(nodeColors).map(([type, c]) => (
            <div key={type} className="flex items-center gap-2">
              <div className={`w-2.5 h-2.5 rounded-sm ${c.dot}`} />
              <span className="text-slate-300">{type.replace(/_/g, " ")}</span>
            </div>
          ))}
        </div>
      </Panel>

      <Panel
        position="top-right"
        className="bg-slate-900/90 border border-slate-700 p-2.5 rounded-lg text-xs backdrop-blur-sm shadow-xl"
      >
        <div className="flex items-center gap-1.5 text-slate-400">
          <Info className="h-3 w-3" /> Click a node to re-center exploration
        </div>
      </Panel>

      <Panel
        position="bottom-left"
        className="bg-slate-900/90 border border-slate-700 p-2.5 rounded-lg text-xs backdrop-blur-sm shadow-xl"
      >
        <div className="flex items-center gap-3 text-slate-400">
          <Badge variant="secondary">{nodeCount} nodes</Badge>
          <Badge variant="secondary">{edgeCount} edges</Badge>
        </div>
      </Panel>
    </ReactFlow>
  );
}

// ── Public component ─────────────────────────────────────────────────────────

export function GraphSubgraph({
  scope,
  initialType,
  initialId,
}: {
  scope: GraphScope;
  initialType?: string;
  initialId?: string;
}) {
  const validInitialType =
    initialType && NODE_TYPE_OPTIONS.includes(initialType as (typeof NODE_TYPE_OPTIONS)[number])
      ? initialType
      : "pattern";
  const [entityType, setEntityType] = useState(validInitialType);
  const [entityId, setEntityId] = useState(initialId ?? "");
  const [maxDepth, setMaxDepth] = useState(1);
  const [queryParams, setQueryParams] = useState<{
    type: string;
    id: string;
    depth: number;
  } | null>(
    initialId ? { type: validInitialType, id: initialId, depth: 1 } : null,
  );

  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);

  const { data, isLoading, error, isFetching } = useQuery<GraphSubgraphResponse>({
    queryKey: [
      "graph-subgraph",
      queryParams?.type,
      queryParams?.id,
      queryParams?.depth,
      scope.domainId,
      scope.asOf,
    ],
    queryFn: () =>
      graphApi.subgraph(
        queryParams!.type,
        queryParams!.id,
        queryParams!.depth,
        scope,
      ),
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
      className: `px-4 py-2 border-2 rounded-lg text-sm transition-all cursor-pointer hover:scale-105 ${getNodeClassName(n.type)}`,
      type: "default",
      position: { x: 0, y: 0 },
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

    const { nodes: laid, edges: laidE } = layoutGraph(rawNodes, rawEdges, {
      nodeWidth: 180,
      nodeHeight: 50,
    });
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
            <GraphNodePicker
              nodeType={entityType}
              nodeId={entityId}
              nodeTypes={NODE_TYPE_OPTIONS}
              onNodeTypeChange={setEntityType}
              onNodeIdChange={setEntityId}
            />

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
            <ReactFlowProvider>
              <FlowCanvas
                nodes={nodes}
                edges={edges}
                onNodesChange={onNodesChange}
                onEdgesChange={onEdgesChange}
                onNodeClick={handleNodeClick}
                nodeCount={data.nodes.length}
                edgeCount={data.edges.length}
              />
            </ReactFlowProvider>
          )}
        </div>
      )}
    </div>
  );
}
