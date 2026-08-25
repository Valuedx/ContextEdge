"use client";

import { useQuery } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { Loader2, Info, Network, HelpCircle, ExternalLink, X } from "lucide-react";
import { useEffect, useCallback, useState } from "react";
import { toast } from "sonner";
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
  Position,
  MarkerType,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import dagre from "dagre";
import { getNodeClassName, edgeColors } from "@/components/graph/graph-constants";
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

// ── Node type → app route mapping ───────────────────────────────────────────

const NODE_ROUTES: Partial<Record<string, string>> = {
  pattern: "/patterns",
  episode: "/episodes",
  playbook: "/playbooks",
  evidence: "/evidence",
  session: "/sessions",
  decision: "/decisions",
  identity: "/identities",
};

// ── Inner canvas — must live inside ReactFlowProvider ───────────────────────

function FlowCanvas({
  nodes,
  edges,
  onNodesChange,
  onEdgesChange,
  onNodeClick,
  hasData,
}: {
  nodes: Node[];
  edges: Edge[];
  onNodesChange: ReturnType<typeof useNodesState>[2];
  onEdgesChange: ReturnType<typeof useEdgesState>[2];
  onNodeClick: (event: React.MouseEvent, node: Node) => void;
  hasData: boolean;
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

  // Re-fit viewport whenever the node set changes
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
    onNodeClick(e, node);
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
      colorMode="system"
    >
      <Background color="#cbd5e1" gap={20} />
      <Controls
        showInteractive={false}
        className="border border-input bg-card fill-foreground text-foreground shadow-sm"
      />

      {/* Top Left: Interactive Node Details & Guidance Panel */}
      {hasData && (
        <Panel
          position="top-left"
          className="max-w-md rounded-lg border bg-card p-4 text-foreground shadow-lg transition-all duration-200"
        >
          {selectedNodeData && activeInfo ? (
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
                  title="Close Selection"
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              </div>
              <h4 className="text-sm font-semibold leading-snug">
                &ldquo;{activeNodeLabel}&rdquo;
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
          ) : hoveredInfo ? (
            <div className="space-y-1.5">
              <div className="flex items-center gap-2 text-primary font-bold text-xs">
                <span>{hoveredInfo.icon}</span>
                <span>{hoveredInfo.label}</span>
              </div>
              <p className="text-xs font-medium line-clamp-1">
                &ldquo;{hoveredNodeLabel}&rdquo;
              </p>
              <p className="text-[11px] text-muted-foreground leading-snug">
                {hoveredInfo.desc}
              </p>
            </div>
          ) : (
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <HelpCircle className="h-4 w-4 text-primary shrink-0" />
              <span>Click any node on the graph to inspect details and open its direct record.</span>
            </div>
          )}
        </Panel>
      )}

      {/* Bottom Panel: Interactive Node Legend */}
      {hasData && (
        <Panel
          position="bottom-center"
          className="flex flex-wrap items-center gap-4 rounded-lg border bg-card px-3.5 py-2 text-[11px] text-muted-foreground shadow-lg"
        >
          <div className="flex items-center gap-1.5">
            <span className="h-2 w-2 rounded-full bg-blue-500"></span>
            <span className="font-semibold text-foreground">Playbook:</span> Fix Guide
          </div>
          <div className="flex items-center gap-1.5">
            <span className="h-2 w-2 rounded-full bg-sky-500"></span>
            <span className="font-semibold text-foreground">Pattern:</span> Recurring Issue
          </div>
          <div className="flex items-center gap-1.5">
            <span className="h-2 w-2 rounded-full bg-emerald-500"></span>
            <span className="font-semibold text-foreground">Episode:</span> Incident Summary
          </div>
          <div className="flex items-center gap-1.5">
            <span className="h-2 w-2 rounded-full bg-purple-400"></span>
            <span className="font-semibold text-foreground">Identity:</span> User / Host
          </div>
          <div className="flex items-center gap-1.5">
            <span className="h-2 w-2 rounded-full bg-slate-400"></span>
            <span className="font-semibold text-foreground">Evidence:</span> Raw Ticket
          </div>
        </Panel>
      )}

      {/* Top Right: Click hint */}
      {hasData && (
        <Panel
          position="top-right"
          className="rounded-lg border bg-card p-2.5 text-xs text-muted-foreground shadow-lg"
        >
          <div className="flex items-center gap-1.5">
            <Info className="h-3 w-3" /> Click a node to open details
          </div>
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
  const router = useRouter();
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);

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
        data: { label: displayLabel },
        className: `px-4 py-2 border-2 rounded-lg text-sm transition-all cursor-pointer hover:scale-105 ${
          getNodeClassName(n.type)
        }${n.type === "pattern" ? " font-bold shadow-[0_0_15px_rgba(99,102,241,0.5)]" : ""}`,
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

  // Navigate to the entity page when a node is clicked
  const handleNodeClick = useCallback(
    (_: React.MouseEvent, node: Node) => {
      const [type, ...idParts] = node.id.split(":");
      const id = idParts.join(":");
      const route = NODE_ROUTES[type];
      
      if (route) {
        router.push(`${route}/${id}`);
      } else {
        toast.info(
          `"${node.data.label}" is an enrichment concept. Dedicated detail pages are available for Episodes, Patterns, and Identities.`,
          { duration: 4000 }
        );
      }
    },
    [router],
  );

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

  return (
    <div className="relative w-full h-[600px] overflow-hidden rounded-lg border bg-card">
      {data?.truncated && (
        <div className="absolute top-2 left-1/2 -translate-x-1/2 z-10 px-3 py-1 rounded-full bg-amber-500/15 border border-amber-500/30 text-amber-300 text-xs font-medium pointer-events-none">
          Large graph — showing the strongest connections only
        </div>
      )}
      <ReactFlowProvider>
        <FlowCanvas
          nodes={nodes}
          edges={edges}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onNodeClick={handleNodeClick}
          hasData={hasData}
        />
      </ReactFlowProvider>
    </div>
  );
}
