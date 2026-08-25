"use client";

import { useQuery } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import {
  Eye,
  EyeOff,
  ExternalLink,
  Info,
  List,
  Loader2,
  Map,
  Maximize2,
  Network,
  X,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
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
  Position,
  MarkerType,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import dagre from "dagre";
import { getNodeClassName, edgeColors, GRAPH_NODE_CARD_CLASS } from "@/components/graph/graph-constants";
import { graphNodeRecordHref } from "@/components/graph/graph-node-routes";
import type { PatternSubgraph } from "@/lib/types";

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

// ── Dagre layout ─────────────────────────────────────────────────────────────

function layoutGraph(nodes: Node[], edges: Edge[], direction = "LR") {
  const g = new dagre.graphlib.Graph();
  g.setDefaultEdgeLabel(() => ({}));
  g.setGraph({ rankdir: direction, nodesep: 60, ranksep: 100 });

  nodes.forEach((n) => g.setNode(n.id, { width: 180, height: 50 }));
  edges.forEach((e) => g.setEdge(e.source, e.target));
  dagre.layout(g);

  const isHorizontal = direction === "LR";
  return {
    nodes: nodes.map((n) => {
      const pos = g.node(n.id);
      return {
        ...n,
        targetPosition: isHorizontal ? Position.Left : Position.Top,
        sourcePosition: isHorizontal ? Position.Right : Position.Bottom,
        position: { x: pos.x - 90, y: pos.y - 25 },
      };
    }),
    edges,
  };
}

const LEGEND_ITEMS = [
  { type: "playbook", label: "Playbook", hint: "Fix Guide", dot: "bg-blue-500" },
  { type: "pattern", label: "Pattern", hint: "Recurring Issue", dot: "bg-sky-500" },
  { type: "episode", label: "Episode", hint: "Incident Summary", dot: "bg-emerald-500" },
  { type: "identity", label: "Identity", hint: "User / Host", dot: "bg-purple-400" },
  { type: "evidence", label: "Evidence", hint: "Raw Ticket", dot: "bg-slate-400" },
] as const;

function nodeTypeOf(node: Node): string {
  if (typeof node.data?.nodeType === "string" && node.data.nodeType) {
    return node.data.nodeType;
  }
  return node.id.split(":")[0] || "";
}

// ── Inner canvas — must live inside ReactFlowProvider ───────────────────────

function FlowCanvas({
  nodes,
  edges,
  onNodesChange,
  onEdgesChange,
  hasData,
  isTruncated,
  graphKey,
  onFullscreen,
  returnTo,
}: {
  nodes: Node[];
  edges: Edge[];
  onNodesChange: ReturnType<typeof useNodesState>[2];
  onEdgesChange: ReturnType<typeof useEdgesState>[2];
  hasData: boolean;
  isTruncated: boolean;
  graphKey: string;
  onFullscreen?: () => void;
  returnTo?: string;
}) {
  const { fitView } = useReactFlow();
  const nodeCount = nodes.length;
  const router = useRouter();
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

  // Re-fit only when new graph data is laid out. React Flow selection also
  // updates `nodes`; depending on the whole array makes every click jump.
  useEffect(() => {
    if (nodeCount > 0) {
      requestAnimationFrame(() =>
        fitView({ padding: 0.15, duration: 350, maxZoom: 1.2 })
      );
    }
  }, [graphKey, nodeCount, fitView]);

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
          ariaLabel="Pattern graph overview"
          position="bottom-right"
          nodeColor={(node) => {
            const type = node.id.split(":")[0];
            if (type === "playbook") return "#2563eb";
            if (type === "pattern") return "#0284c7";
            if (type === "episode") return "#16a34a";
            if (type === "evidence") return "#64748b";
            if (type === "identity") return "#a855f7";
            return "#94a3b8";
          }}
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
              {isTruncated && (
                <p className="mt-2 rounded-md border border-amber-500/30 bg-amber-500/10 px-2 py-1.5 text-[11px] font-medium text-amber-700 dark:text-amber-300">
                  Showing key connections for this large graph.
                </p>
              )}
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
            {redirectRoute ? (
              <button
                type="button"
                onClick={() => router.push(redirectRoute)}
                className="mt-2 inline-flex items-center gap-1.5 rounded-md bg-action px-3 py-1.5 text-xs font-medium text-action-foreground transition-colors hover:bg-action/90"
              >
                <span>View Details & Redirect</span>
                <ExternalLink className="h-3.5 w-3.5" />
              </button>
            ) : (
              <p className="text-[11px] text-muted-foreground italic pt-1">
                Enrichment concept node — inspect connected Episodes or Patterns for deep details.
              </p>
            )}
          </div>
        </Panel>
      )}

      {hasData && legendOpen && (
        <Panel
          position="bottom-center"
          className="nodrag nopan nowheel flex flex-wrap items-center gap-2 rounded-lg border bg-card px-3.5 py-2 text-[11px] text-muted-foreground shadow-lg"
          onMouseDown={(event) => event.stopPropagation()}
        >
          {LEGEND_ITEMS.map((item) => {
            const active = typeFilter === item.type;
            return (
              <button
                key={item.type}
                type="button"
                onClick={(event) => {
                  event.stopPropagation();
                  setTypeFilter((current) => (current === item.type ? null : item.type));
                  setSelectedNodeData(null);
                }}
                aria-pressed={active}
                title={
                  active
                    ? `Showing only ${item.label} nodes — click again to show all`
                    : `Show only ${item.label} nodes`
                }
                className={`inline-flex items-center gap-1.5 rounded-md px-2 py-1 transition-colors ${
                  active
                    ? "bg-muted text-foreground ring-1 ring-ring"
                    : "hover:bg-muted/70 hover:text-foreground"
                }`}
              >
                <span className={`h-2 w-2 rounded-full ${item.dot}`} />
                <span className="font-semibold text-foreground">{item.label}:</span>
                {item.hint}
              </button>
            );
          })}
        </Panel>
      )}

      {/* Empty state overlay */}
      {!hasData && (
        <div className="absolute inset-0 flex items-center justify-center pointer-events-none z-10">
          <div className="max-w-sm rounded-lg border bg-card p-6 text-center shadow-lg animate-in fade-in zoom-in duration-300">
            <div className="relative mx-auto h-16 w-16 mb-4">
              <Network className="h-16 w-16 text-primary opacity-20 animate-pulse" />
              <Info className="absolute inset-0 m-auto h-8 w-8 text-primary" />
            </div>
            <h3 className="text-lg font-bold text-foreground">Pattern Seed Identified</h3>
            <p className="text-sm text-muted-foreground mt-2 leading-relaxed">
              This operational pattern has been successfully discovered.
              Cluster more episodes to reveal the full knowledge graph.
            </p>
            <div className="mt-4 flex justify-center">
              <span className="rounded border bg-muted px-2 py-1 text-[10px] font-mono uppercase tracking-wider text-muted-foreground">
                Awaiting Context
              </span>
            </div>
          </div>
        </div>
      )}
    </ReactFlow>
  );
}

// ── Public component ─────────────────────────────────────────────────────────

export function PatternGraph({ patternId }: { patternId: string }) {
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [fullscreenOpen, setFullscreenOpen] = useState(false);

  const { data, isLoading, error } = useQuery<PatternSubgraph>({
    queryKey: ["pattern-graph", patternId],
    queryFn: () => api.get<PatternSubgraph>(`/patterns/${patternId}/graph`),
  });

  useEffect(() => {
    if (!data?.nodes) return;

    const rawNodes: Node[] = data.nodes.map((n) => {
      const descObj = NODE_DESCRIPTIONS[n.type];
      const tooltipText = descObj
        ? `${descObj.label}: ${descObj.desc}`
        : `${n.type.toUpperCase()}: ${n.title || ""}`;

      const displayLabel =
        n.title && n.title.toUpperCase() !== n.type.toUpperCase()
          ? n.title
          : `${n.type.toUpperCase()} (${n.id.slice(0, 8)})`;

      return {
        id: `${n.type}:${n.id}`,
        data: { label: displayLabel, nodeType: n.type, rawId: n.id },
        className: `${GRAPH_NODE_CARD_CLASS} ${getNodeClassName(n.type)}${
          n.type === "pattern" ? " font-bold" : ""
        }`,
        type: "default",
        position: { x: 0, y: 0 },
        // Native browser hover tooltip attribute
        title: tooltipText,
      };
    });

    const rawEdges: Edge[] = data.edges.map((e, i) => {
      const ec = edgeColors[e.type] || { stroke: "#475569" };
      return {
        id: `e-${i}`,
        source: e.source,
        target: e.target,
        label: e.type.replace(/_/g, " "),
        labelStyle: { fill: "#94a3b8", fontSize: "10px", fontWeight: 500 },
        style: { stroke: ec.stroke, strokeDasharray: ec.dasharray },
        animated: true,
        markerEnd: { type: MarkerType.ArrowClosed, color: ec.stroke },
      };
    });

    const { nodes: laid, edges: laidEdges } = layoutGraph(rawNodes, rawEdges);
    setNodes(laid);
    setEdges(laidEdges);
  }, [data, setNodes, setEdges]);

  if (isLoading) {
    return (
      <div className="flex justify-center p-12">
        <Loader2 className="animate-spin h-8 w-8 text-primary" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="text-destructive p-4 border rounded-lg bg-destructive/10">
        Failed to load graph
      </div>
    );
  }

  const hasData = nodes.length > 1;
  const graphKey = `${patternId}:${data?.nodes.length ?? 0}:${data?.edges.length ?? 0}:${data?.truncated ? "truncated" : "full"}`;

  return (
    <>
      <div className="relative w-full h-[600px] overflow-hidden rounded-lg border bg-card">
        <ReactFlowProvider>
          <FlowCanvas
            nodes={nodes}
            edges={edges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            hasData={hasData}
            isTruncated={Boolean(data?.truncated)}
            graphKey={graphKey}
            onFullscreen={() => setFullscreenOpen(true)}
            returnTo={`/patterns/${patternId}`}
          />
        </ReactFlowProvider>
      </div>

      {fullscreenOpen && (
        <div
          className="fixed inset-0 z-50 bg-background"
          role="dialog"
          aria-modal="true"
          aria-label="Pattern graph fullscreen"
        >
          <div className="flex h-12 items-center justify-between border-b bg-card px-4 shadow-sm">
            <div className="min-w-0">
              <p className="truncate text-sm font-semibold text-foreground">
                Pattern graph
              </p>
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
                hasData={hasData}
                isTruncated={Boolean(data?.truncated)}
                graphKey={`fullscreen:${graphKey}`}
                returnTo={`/patterns/${patternId}`}
              />
            </ReactFlowProvider>
          </div>
        </div>
      )}
    </>
  );
}
