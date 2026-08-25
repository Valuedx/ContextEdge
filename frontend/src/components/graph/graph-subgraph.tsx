"use client";

import { useState, useEffect, useCallback, useMemo, useRef } from "react";
import { useQuery } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { graphApi } from "@/lib/graph-api";
import type { GraphScope, GraphSubgraphResponse } from "@/lib/types/graph";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  ChevronDown,
  Eye,
  EyeOff,
  ExternalLink,
  Info,
  List,
  Loader2,
  Map,
  Maximize2,
  Network,
  Search,
  X,
} from "lucide-react";
import {
  ReactFlow,
  ReactFlowProvider,
  useNodesState,
  useEdgesState,
  useReactFlow,
  Background,
  Controls,
  MiniMap,
  Panel,
  Node,
  Edge,
  MarkerType,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { getNodeClassName, edgeColors, nodeColors, NODE_TYPE_OPTIONS, GRAPH_NODE_CARD_CLASS } from "./graph-constants";
import { backLabelForPath, graphNodeRecordHref } from "./graph-node-routes";
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

function nodeTypeOf(node: Node): string {
  if (typeof node.data?.nodeType === "string" && node.data.nodeType) {
    return node.data.nodeType;
  }
  return node.id.split(":")[0] || "";
}

// ── Inner canvas — must live inside ReactFlowProvider ────────────────────────

const LEGEND_HINTS: Record<string, string> = {
  playbook: "Fix Guide",
  pattern: "Recurring Issue",
  episode: "Incident Summary",
  identity: "User / Host",
  evidence: "Raw Ticket",
};

function minimapColor(node: Node): string {
  const type = nodeTypeOf(node);
  if (type === "playbook") return "#2563eb";
  if (type === "pattern") return "#0284c7";
  if (type === "episode") return "#16a34a";
  if (type === "evidence") return "#64748b";
  if (type === "identity") return "#a855f7";
  return "#94a3b8";
}

function FlowCanvas({
  nodes,
  edges,
  onNodesChange,
  onEdgesChange,
  onNodeClick,
  nodeCount,
  graphKey,
  onFullscreen,
  returnTo,
}: {
  nodes: Node[];
  edges: Edge[];
  onNodesChange: ReturnType<typeof useNodesState>[2];
  onEdgesChange: ReturnType<typeof useEdgesState>[2];
  onNodeClick: (event: React.MouseEvent, node: Node) => void;
  nodeCount: number;
  graphKey: string;
  onFullscreen?: () => void;
  returnTo?: string;
}) {
  const { fitView } = useReactFlow();
  const router = useRouter();
  const hasData = nodes.length > 0;

  const [selectedNodeData, setSelectedNodeData] = useState<{
    id: string;
    type: string;
    rawId: string;
    label: string;
  } | null>(null);
  const [hintOpen, setHintOpen] = useState(false);
  const [legendOpen, setLegendOpen] = useState(true);
  const [miniMapOpen, setMiniMapOpen] = useState(true);
  const [typeFilter, setTypeFilter] = useState<string | null>(null);

  const visibleNodes = useMemo(() => {
    if (!typeFilter) return nodes;
    return nodes.filter((node) => nodeTypeOf(node) === typeFilter);
  }, [nodes, typeFilter]);

  const visibleEdges = useMemo(() => {
    if (!typeFilter) return edges;
    const visibleIds = new Set(visibleNodes.map((node) => node.id));
    return edges.filter(
      (edge) => visibleIds.has(edge.source) && visibleIds.has(edge.target),
    );
  }, [edges, typeFilter, visibleNodes]);

  const legendTypes = useMemo(() => {
    const present = new Set(nodes.map(nodeTypeOf));
    return Object.entries(nodeColors).filter(([type]) => present.has(type));
  }, [nodes]);

  const skipFilterFit = useRef(true);
  useEffect(() => {
    if (skipFilterFit.current) {
      skipFilterFit.current = false;
      return;
    }
    requestAnimationFrame(() =>
      fitView({ padding: 0.2, duration: 250, maxZoom: 1.4 })
    );
  }, [typeFilter, fitView]);

  useEffect(() => {
    if (nodes.length > 0) {
      requestAnimationFrame(() =>
        fitView({ padding: 0.15, duration: 350, maxZoom: 1.2 })
      );
    }
  }, [graphKey, nodeCount, fitView]);

  const handleCanvasNodeClick = (event: React.MouseEvent, node: Node) => {
    if (event.detail === 2) return;
    const storedType = typeof node.data?.nodeType === "string" ? node.data.nodeType : "";
    const storedRawId = typeof node.data?.rawId === "string" ? node.data.rawId : "";
    const [typeFromId, ...idParts] = node.id.split(":");
    const type = storedType || typeFromId;
    const rawId = storedRawId || idParts.join(":");
    const label = String(node.data?.label || node.id);
    setSelectedNodeData({ id: node.id, type, rawId, label });
    setHintOpen(false);
  };

  const handleOpenFullscreen = () => {
    if (!onFullscreen) return;
    setSelectedNodeData(null);
    setHintOpen(false);
    onFullscreen();
  };

  const activeInfo = selectedNodeData
    ? NODE_DESCRIPTIONS[selectedNodeData.type] ?? {
        label: selectedNodeData.type.replace(/_/g, " ").toUpperCase() || "NODE",
        desc: "Selected graph node. Open the linked record when a route is available.",
        icon: "●",
      }
    : null;

  const handleRecenter = (e: React.MouseEvent) => {
    if (!selectedNodeData) return;
    const dummyNode: Node = {
      id: selectedNodeData.id,
      position: { x: 0, y: 0 },
      data: { label: selectedNodeData.label },
    };
    onNodeClick(e, dummyNode);
  };

  const redirectRoute = selectedNodeData
    ? graphNodeRecordHref(selectedNodeData.type, selectedNodeData.rawId, { from: returnTo })
    : null;

  return (
    <ReactFlow
      nodes={visibleNodes}
      edges={visibleEdges}
      onNodesChange={onNodesChange}
      onEdgesChange={onEdgesChange}
      onNodeClick={handleCanvasNodeClick}
      onNodeDoubleClick={() => handleOpenFullscreen()}
      onPaneClick={() => {
        setSelectedNodeData(null);
        setHintOpen(false);
      }}
      onDoubleClick={(event) => {
        const target = event.target as HTMLElement;
        if (target.closest(".react-flow__panel")) return;
        handleOpenFullscreen();
      }}
      nodesFocusable
      elementsSelectable
      selectNodesOnDrag={false}
      colorMode="system"
    >
      <Background color="#cbd5e1" gap={20} />
      <Controls
        position="top-left"
        showInteractive={false}
        className="border border-input bg-card fill-foreground text-foreground shadow-sm"
      />

      {hasData && miniMapOpen && (
        <MiniMap
          pannable
          zoomable
          ariaLabel="Subgraph overview"
          position="bottom-right"
          nodeColor={minimapColor}
          nodeStrokeWidth={3}
          maskColor="rgb(15 23 42 / 0.12)"
          className="hidden rounded-lg border border-input bg-card shadow-lg md:block"
        />
      )}

      {hasData && (
        <Panel
          position="top-right"
          className="relative flex items-center gap-1 rounded-lg border bg-card p-1 text-foreground shadow-lg"
        >
          <button
            type="button"
            onClick={() => {
              if (!selectedNodeData) return;
              setSelectedNodeData(null);
            }}
            disabled={!selectedNodeData}
            className="inline-flex h-8 items-center gap-1.5 rounded-md px-2 text-xs font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:pointer-events-none disabled:opacity-40"
            title={selectedNodeData ? "Hide details panel" : "Click a node to view details"}
          >
            {selectedNodeData ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
            Details
          </button>
          <button
            type="button"
            onClick={() => setLegendOpen((value) => !value)}
            className="inline-flex h-8 items-center gap-1.5 rounded-md px-2 text-xs font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            title={legendOpen ? "Hide legend" : "Show legend"}
          >
            <List className="h-3.5 w-3.5" />
            Legend
          </button>
          <button
            type="button"
            onClick={() => setMiniMapOpen((value) => !value)}
            className="inline-flex h-8 items-center gap-1.5 rounded-md px-2 text-xs font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            title={miniMapOpen ? "Hide minimap" : "Show minimap"}
          >
            <Map className="h-3.5 w-3.5" />
            Map
          </button>
          <button
            type="button"
            onClick={() => {
              if (onFullscreen) {
                onFullscreen();
                return;
              }
              fitView({ padding: 0.18, duration: 250, maxZoom: 1.2 });
            }}
            className="inline-flex h-8 items-center gap-1.5 rounded-md px-2 text-xs font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            title={onFullscreen ? "Open fullscreen graph" : "Fit graph to view"}
          >
            <Maximize2 className="h-3.5 w-3.5" />
            Full
          </button>
          <button
            type="button"
            onClick={() => setHintOpen((value) => !value)}
            className="inline-flex h-8 w-8 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            title="Graph tips"
            aria-label="Graph tips"
            aria-expanded={hintOpen}
          >
            <Info className="h-3.5 w-3.5" />
          </button>
          {hintOpen && (
            <div className="absolute right-0 top-full z-20 mt-1 w-64 rounded-lg border bg-card p-3 text-xs text-muted-foreground shadow-lg">
              <p>Click any node to inspect details. Double-click the graph to open fullscreen.</p>
            </div>
          )}
        </Panel>
      )}

      {selectedNodeData && activeInfo && (
        <Panel
          position="top-center"
          className="max-w-md rounded-lg border bg-card p-4 text-foreground shadow-lg transition-all duration-200"
        >
          <div className="space-y-2">
            <div className="flex items-center justify-between gap-2">
              <div className="flex items-center gap-2 text-primary font-bold text-xs uppercase tracking-wider">
                <span>{activeInfo.icon}</span>
                <span>{activeInfo.label}</span>
              </div>
              <button
                type="button"
                onClick={() => setSelectedNodeData(null)}
                className="rounded p-0.5 text-muted-foreground hover:text-foreground"
                title="Close details panel"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </div>
            <h4 className="text-sm font-semibold leading-snug">
              &ldquo;{selectedNodeData.label}&rdquo;
            </h4>
            <p className="text-xs text-muted-foreground leading-relaxed">
              {activeInfo.desc}
            </p>
            <div className="flex flex-wrap items-center gap-2 pt-1">
              {redirectRoute ? (
                <button
                  type="button"
                  onClick={() => router.push(redirectRoute)}
                  className="inline-flex items-center gap-1.5 rounded-md bg-action px-3 py-1.5 text-xs font-medium text-action-foreground transition-colors hover:bg-action/90"
                >
                  <span>View Details & Redirect</span>
                  <ExternalLink className="h-3.5 w-3.5" />
                </button>
              ) : (
                <p className="text-[11px] text-muted-foreground italic">
                  Enrichment concept node — inspect connected records for deep details.
                </p>
              )}
              <button
                type="button"
                onClick={handleRecenter}
                className="inline-flex items-center gap-1.5 rounded-md border border-input bg-card px-2.5 py-1.5 text-xs font-medium transition-colors hover:bg-accent hover:text-accent-foreground"
              >
                Re-center here
              </button>
            </div>
          </div>
        </Panel>
      )}

      {hasData && legendOpen && (
        <Panel
          position="bottom-center"
          className="nodrag nopan nowheel flex flex-wrap items-center gap-2 rounded-lg border bg-card px-3.5 py-2 text-[11px] text-muted-foreground shadow-lg"
          onMouseDown={(event) => event.stopPropagation()}
        >
          {legendTypes.map(([type, c]) => {
            const active = typeFilter === type;
            const label = type.replace(/_/g, " ");
            const hint = LEGEND_HINTS[type];
            return (
              <button
                key={type}
                type="button"
                onClick={(event) => {
                  event.stopPropagation();
                  setTypeFilter((current) => (current === type ? null : type));
                  setSelectedNodeData(null);
                }}
                aria-pressed={active}
                title={
                  active
                    ? `Showing only ${label} nodes — click again to show all`
                    : `Show only ${label} nodes`
                }
                className={`inline-flex items-center gap-1.5 rounded-md px-2 py-1 transition-colors ${
                  active
                    ? "bg-muted text-foreground ring-1 ring-ring"
                    : "hover:bg-muted/70 hover:text-foreground"
                }`}
              >
                <span className={`h-2 w-2 rounded-full ${c.dot}`} />
                <span className="font-semibold capitalize text-foreground">{label}{hint ? ":" : ""}</span>
                {hint}
              </button>
            );
          })}
        </Panel>
      )}
    </ReactFlow>
  );
}

// ── Public component ─────────────────────────────────────────────────────────

export function GraphSubgraph({
  scope,
  initialType,
  initialId,
  returnTo,
}: {
  scope: GraphScope;
  initialType?: string;
  initialId?: string;
  returnTo?: string;
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
  const [fullscreenOpen, setFullscreenOpen] = useState(false);

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
        data: { label: displayLabel, nodeType: n.type, rawId: n.id },
        className: `${GRAPH_NODE_CARD_CLASS} ${getNodeClassName(n.type)}`,
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

  const graphKey = `${queryParams?.type}:${queryParams?.id}:${queryParams?.depth}:${data?.nodes.length ?? 0}:${data?.edges.length ?? 0}`;

  return (
    <div className="space-y-4">
      <Card size="sm">
        <CardHeader className="pb-0">
          <div className="flex items-center justify-between gap-3">
            <CardTitle className="flex items-center gap-2">
              <Search className="h-4 w-4" /> Explore Subgraph
            </CardTitle>
            {returnTo ? (
              <Link
                href={returnTo}
                className="text-sm font-medium text-muted-foreground hover:text-foreground"
              >
                {backLabelForPath(returnTo)}
              </Link>
            ) : null}
          </div>
        </CardHeader>
        <CardContent>
          <div className="flex flex-wrap items-end gap-2.5">
            <GraphNodePicker
              nodeType={entityType}
              nodeId={entityId}
              nodeTypes={NODE_TYPE_OPTIONS}
              onNodeTypeChange={setEntityType}
              onNodeIdChange={setEntityId}
            />

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

            <Button size="sm" className="h-8" onClick={handleExplore} disabled={!entityId.trim() || isFetching}>
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

      {isLoading && <Skeleton className="h-[600px] rounded-lg" />}

      {error && (
        <div className="text-destructive p-4 border rounded-lg bg-destructive/10">
          {(error as Error).message || "Failed to load subgraph"}
        </div>
      )}

      {data && !isLoading && (
        <div className="relative w-full h-[600px] overflow-hidden rounded-lg border bg-card">
          {data.nodes.length === 0 ? (
            <div className="absolute inset-0 flex items-center justify-center">
              <div className="text-center space-y-3">
                <Network className="h-12 w-12 text-muted-foreground mx-auto" />
                <p className="text-muted-foreground text-sm">No graph data found for this entity.</p>
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
                graphKey={graphKey}
                onFullscreen={() => setFullscreenOpen(true)}
                returnTo={returnTo}
              />
            </ReactFlowProvider>
          )}
        </div>
      )}

      {fullscreenOpen && data && data.nodes.length > 0 && (
        <div
          className="fixed inset-0 z-50 bg-background"
          role="dialog"
          aria-modal="true"
          aria-label="Subgraph fullscreen"
        >
          <div className="flex h-12 items-center justify-between border-b bg-card px-4 shadow-sm">
            <div className="min-w-0">
              <p className="truncate text-sm font-semibold text-foreground">Graph explorer</p>
              <p className="text-xs text-muted-foreground">
                Explore nodes, connections, and source records
              </p>
            </div>
            <button
              type="button"
              onClick={() => setFullscreenOpen(false)}
              className="inline-flex h-9 w-9 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
              aria-label="Close fullscreen graph"
              title="Close"
            >
              <X className="h-5 w-5" />
            </button>
          </div>
          <div className="h-[calc(100vh-3rem)]">
            <ReactFlowProvider>
              <FlowCanvas
                nodes={nodes}
                edges={edges}
                onNodesChange={onNodesChange}
                onEdgesChange={onEdgesChange}
                onNodeClick={handleNodeClick}
                nodeCount={data?.nodes.length ?? 0}
                graphKey={`fullscreen:${graphKey}`}
                returnTo={returnTo}
              />
            </ReactFlowProvider>
          </div>
        </div>
      )}
    </div>
  );
}
