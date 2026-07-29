"use client";

import { useQuery } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { Loader2, Info, Network, HelpCircle, FileText, Activity, BookOpen, Layers } from "lucide-react";
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

// ── Node Descriptions for Hover Tooltips & Guidance ───────────────────────────

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
    desc: "Original raw ticket/log message pulled directly from ServiceNow, Jira, or email.",
    icon: "📄",
  },
  entity: {
    label: "ENTITY (System Asset)",
    desc: "Operational noun (Hostname, IP address, or application name).",
    icon: "🏷️",
  },
  policy: {
    label: "POLICY (Governance Rule)",
    desc: "Access control or approval gate policy rule.",
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
  const [hoveredNodeType, setHoveredNodeType] = useState<string | null>(null);
  const [hoveredNodeLabel, setHoveredNodeLabel] = useState<string | null>(null);

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

  const hoveredInfo = hoveredNodeType ? NODE_DESCRIPTIONS[hoveredNodeType] : null;

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      onNodesChange={onNodesChange}
      onEdgesChange={onEdgesChange}
      onNodeClick={onNodeClick}
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

      {/* Top Left: Interactive Node Explanation Panel (Shown on Cursor Hover) */}
      {hasData && (
        <Panel
          position="top-left"
          className="bg-slate-900/95 border border-slate-700 p-3 rounded-xl max-w-md backdrop-blur-md shadow-2xl transition-all duration-200"
        >
          {hoveredInfo ? (
            <div className="space-y-1">
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
            <div className="flex items-center gap-2 text-xs text-slate-400">
              <HelpCircle className="h-4 w-4 text-indigo-400 shrink-0" />
              <span>Hover your cursor over any node to see what it means.</span>
            </div>
          )}
        </Panel>
      )}

      {/* Bottom Panel: Interactive Node Legend */}
      {hasData && (
        <Panel
          position="bottom-center"
          className="bg-slate-900/90 border border-slate-800 px-3 py-1.5 rounded-full text-[11px] backdrop-blur-sm shadow-lg flex items-center gap-4 text-slate-300"
        >
          <div className="flex items-center gap-1.5">
            <span className="h-2 w-2 rounded-full bg-blue-500"></span>
            <span className="font-semibold text-blue-400">Playbook:</span> Verified Fix
          </div>
          <div className="flex items-center gap-1.5">
            <span className="h-2 w-2 rounded-full bg-indigo-500"></span>
            <span className="font-semibold text-indigo-400">Pattern:</span> Recurring Issue
          </div>
          <div className="flex items-center gap-1.5">
            <span className="h-2 w-2 rounded-full bg-emerald-500"></span>
            <span className="font-semibold text-emerald-400">Episode:</span> AI Summary
          </div>
          <div className="flex items-center gap-1.5">
            <span className="h-2 w-2 rounded-full bg-slate-400"></span>
            <span className="font-semibold text-slate-300">Evidence:</span> Raw Ticket
          </div>
        </Panel>
      )}

      {/* Top Right: Click hint */}
      {hasData && (
        <Panel
          position="top-right"
          className="bg-slate-900/90 border border-slate-700 p-2.5 rounded-lg text-xs backdrop-blur-sm shadow-xl"
        >
          <div className="flex items-center gap-1.5 text-slate-400">
            <Info className="h-3 w-3" /> Click a node to open details
          </div>
        </Panel>
      )}

      {/* Empty state overlay */}
      {!hasData && (
        <div className="absolute inset-0 flex items-center justify-center pointer-events-none z-10">
          <div className="bg-slate-900/95 border border-slate-700 p-6 rounded-2xl text-center max-w-sm shadow-2xl animate-in fade-in zoom-in duration-300">
            <div className="relative mx-auto h-16 w-16 mb-4">
              <Network className="h-16 w-16 text-indigo-400 opacity-20 animate-pulse" />
              <Info className="absolute inset-0 m-auto h-8 w-8 text-indigo-400" />
            </div>
            <h3 className="text-lg font-bold text-slate-100">Pattern Seed Identified</h3>
            <p className="text-sm text-slate-400 mt-2 leading-relaxed">
              This operational pattern has been successfully discovered.
              Cluster more episodes to reveal the full knowledge graph.
            </p>
            <div className="mt-4 flex justify-center">
              <span className="px-2 py-1 bg-indigo-500/10 text-indigo-400 border border-indigo-500/20 rounded text-[10px] font-mono uppercase tracking-wider">
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

      return {
        id: `${n.type}:${n.id}`,
        data: { label: n.title || n.type.toUpperCase() },
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
        <Loader2 className="animate-spin h-8 w-8 text-indigo-500" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="text-destructive p-4 border rounded-xl bg-destructive/10">
        Failed to load graph
      </div>
    );
  }

  const hasData = nodes.length > 1;

  return (
    <div className="relative w-full h-[600px] border rounded-xl bg-[#020617] overflow-hidden">
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
