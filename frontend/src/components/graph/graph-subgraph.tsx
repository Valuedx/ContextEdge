"use client";

import { useState, useEffect, useCallback } from "react";
import { useQuery } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { graphApi } from "@/lib/graph-api";
import type { GraphScope, GraphSubgraphResponse } from "@/lib/types/graph";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";
import { Loader2, Network, Search, Info, ExternalLink, X, HelpCircle } from "lucide-react";
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

// ── Node Descriptions for Hover & Selected Node Inspector ────────────────────

const NODE_DESCRIPTIONS: Record<string, { label: string; desc: string; icon: string }> = {
  playbook: {
    label: "PLAYBOOK (Verified Fix Guide)",
    desc: "Step-by-step operational instructions to safely fix the issue.",
    icon: "📘",
  },
  pattern: {
    label: "PATTERN (Recurring Problem)",
    desc: "Title of the recurring systemic problem identified across repeating incidents.",
    icon: "🟣",
  },
  episode: {
    label: "EPISODE (AI Incident Analysis)",
    desc: "Clean AI summary of ONE specific outage event (Problem + Root Cause + Resolution Fix).",
    icon: "🟢",
  },
  evidence: {
    label: "EVIDENCE (Raw Proof)",
    desc: "Original raw ticket or log message pulled directly from Zoho Desk, ServiceNow, or email.",
    icon: "📄",
  },
  entity: {
    label: "ENTITY (System Asset)",
    desc: "Operational noun (Hostname, IP address, or application name).",
    icon: "🏷️",
  },
  identity: {
    label: "IDENTITY (User / Service Account)",
    desc: "Unique identifier for user, service account, host, or system principal.",
    icon: "👤",
  },
  step: {
    label: "STEP (Resolution Step)",
    desc: "Specific diagnostic action or resolution step executed during incident.",
    icon: "📋",
  },
  root_cause: {
    label: "ROOT CAUSE (Failure Reason)",
    desc: "Core underlying failure driver or root cause identified for this incident.",
    icon: "🔍",
  },
  trigger_condition: {
    label: "TRIGGER (Activation Event)",
    desc: "Symptom, error message, or condition that activates this pattern.",
    icon: "⚡",
  },
  policy: {
    label: "POLICY (Governance Rule)",
    desc: "Access control, risk tier, or approval gate policy rule.",
    icon: "🛡️",
  },
};

const NODE_ROUTES: Partial<Record<string, string>> = {
  pattern: "/patterns",
  episode: "/episodes",
  playbook: "/playbooks",
  evidence: "/evidence",
  session: "/sessions",
  decision: "/decisions",
  identity: "/identities",
};

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
  const router = useRouter();

  const [hoveredNodeType, setHoveredNodeType] = useState<string | null>(null);
  const [hoveredNodeLabel, setHoveredNodeLabel] = useState<string | null>(null);
  const [selectedNodeData, setSelectedNodeData] = useState<{
    id: string;
    type: string;
    rawId: string;
    label: string;
  } | null>(null);

  useEffect(() => {
    if (nodes.length > 0) {
      requestAnimationFrame(() =>
        fitView({ padding: 0.15, duration: 350, maxZoom: 1.2 })
      );
    }
  }, [nodes, fitView]);

  const handleNodeMouseEnter = (_: React.MouseEvent, node: Node) => {
    const nodeType = node.id.split(":")[0];
    setHoveredNodeType(nodeType);
    setHoveredNodeLabel(String(node.data?.label || ""));
  };

  const handleNodeMouseLeave = () => {
    setHoveredNodeType(null);
    setHoveredNodeLabel(null);
  };

  const handleCanvasNodeClick = (e: React.MouseEvent, node: Node) => {
    const [type, ...idParts] = node.id.split(":");
    const rawId = idParts.join(":");
    const label = String(node.data?.label || "");
    setSelectedNodeData({ id: node.id, type, rawId, label });
  };

  const handleRecenter = (e: React.MouseEvent) => {
    if (!selectedNodeData) return;
    const dummyNode: Node = {
      id: selectedNodeData.id,
      position: { x: 0, y: 0 },
      data: { label: selectedNodeData.label },
    };
    onNodeClick(e, dummyNode);
  };

  const activeNodeType = selectedNodeData ? selectedNodeData.type : hoveredNodeType;
  const activeNodeLabel = selectedNodeData ? selectedNodeData.label : hoveredNodeLabel;
  const activeInfo = activeNodeType ? NODE_DESCRIPTIONS[activeNodeType] : null;
  const hoveredInfo = hoveredNodeType ? NODE_DESCRIPTIONS[hoveredNodeType] : null;

  const redirectRoute = selectedNodeData && NODE_ROUTES[selectedNodeData.type]
    ? `${NODE_ROUTES[selectedNodeData.type]}/${selectedNodeData.rawId}`
    : null;

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      onNodesChange={onNodesChange}
      onEdgesChange={onEdgesChange}
      onNodeClick={handleCanvasNodeClick}
      onNodeMouseEnter={handleNodeMouseEnter}
      onNodeMouseLeave={handleNodeMouseLeave}
      fitView
      colorMode="dark"
    >
      <Background color="#1e293b" gap={20} />
      <Controls
        showInteractive={false}
        className="bg-slate-900 border-slate-700 fill-slate-200"
      />

      {/* Top Left: Interactive Node Details & Inspector Overlay */}
      <Panel
        position="top-left"
        className="bg-slate-900/95 border border-indigo-500/40 p-4 rounded-xl max-w-md backdrop-blur-md shadow-2xl transition-all duration-200"
      >
        {selectedNodeData && activeInfo ? (
          <div className="space-y-2">
            <div className="flex items-center justify-between gap-2">
              <div className="flex items-center gap-2 text-indigo-400 font-bold text-xs uppercase tracking-wider">
                <span>{activeInfo.icon}</span>
                <span>{activeInfo.label}</span>
              </div>
              <button
                type="button"
                onClick={() => setSelectedNodeData(null)}
                className="text-slate-400 hover:text-slate-200 p-0.5 rounded"
                title="Close Selection"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </div>
            <h4 className="text-sm font-semibold text-slate-100 leading-snug">
              "{activeNodeLabel}"
            </h4>
            <p className="text-xs text-slate-300 leading-relaxed">
              {activeInfo.desc}
            </p>
            <div className="flex flex-wrap items-center gap-2 pt-1">
              {redirectRoute ? (
                <button
                  type="button"
                  onClick={() => router.push(redirectRoute)}
                  className="inline-flex items-center gap-1.5 rounded-md bg-indigo-600 hover:bg-indigo-500 px-3 py-1.5 text-xs font-medium text-white transition-colors"
                >
                  <span>View Details & Redirect</span>
                  <ExternalLink className="h-3.5 w-3.5" />
                </button>
              ) : null}
              <button
                type="button"
                onClick={handleRecenter}
                className="inline-flex items-center gap-1.5 rounded-md border border-slate-700 bg-slate-800 hover:bg-slate-700 px-2.5 py-1.5 text-xs font-medium text-slate-200 transition-colors"
              >
                <span>Re-center Graph Here 🎯</span>
              </button>
            </div>
          </div>
        ) : hoveredInfo ? (
          <div className="space-y-1.5">
            <div className="flex items-center gap-2 text-indigo-400 font-bold text-xs">
              <span>{hoveredInfo.icon}</span>
              <span>{hoveredInfo.label}</span>
            </div>
            <p className="text-xs text-slate-200 font-medium line-clamp-1">
              "{hoveredNodeLabel}"
            </p>
            <p className="text-[11px] text-slate-400 leading-snug">
              {hoveredInfo.desc}
            </p>
          </div>
        ) : (
          <div className="flex items-center gap-2 text-xs text-slate-300">
            <HelpCircle className="h-4 w-4 text-indigo-400 shrink-0" />
            <span>Click any node on the graph to inspect details and open its direct record.</span>
          </div>
        )}
      </Panel>

      {/* Top Right: Node & Edge counts */}
      <Panel
        position="top-right"
        className="bg-slate-900/90 border border-slate-700 p-2 rounded-lg text-xs backdrop-blur-sm shadow-xl"
      >
        <div className="flex items-center gap-2 text-slate-300 font-mono text-[11px]">
          <Badge variant="secondary" className="px-2 py-0.5">{nodeCount} nodes</Badge>
          <Badge variant="secondary" className="px-2 py-0.5">{edgeCount} edges</Badge>
        </div>
      </Panel>

      {/* Bottom Panel: Interactive Node Legend */}
      <Panel
        position="bottom-center"
        className="bg-slate-900/90 border border-slate-800 px-3.5 py-1.5 rounded-full text-[11px] backdrop-blur-sm shadow-lg flex flex-wrap items-center gap-4 text-slate-300"
      >
        {Object.entries(nodeColors).map(([type, c]) => (
          <div key={type} className="flex items-center gap-1.5">
            <span className={`h-2 w-2 rounded-full ${c.dot}`}></span>
            <span className="font-semibold text-slate-200 capitalize">{type.replace(/_/g, " ")}</span>
          </div>
        ))}
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

    const rawNodes: Node[] = data.nodes.map((n) => {
      const displayLabel =
        n.title && n.title.toUpperCase() !== n.type.toUpperCase()
          ? n.title
          : `${n.type.replace(/_/g, " ").toUpperCase()} (${n.id.slice(0, 8)})`;

      return {
        id: `${n.type}:${n.id}`,
        data: { label: displayLabel },
        className: `px-4 py-2 border-2 rounded-lg text-sm transition-all cursor-pointer hover:scale-105 ${getNodeClassName(n.type)}`,
        type: "default",
        position: { x: 0, y: 0 },
      };
    });

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
