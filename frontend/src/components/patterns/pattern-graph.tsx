"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Loader2, Info, Network, ZoomIn, Maximize } from "lucide-react";
import { useEffect, useMemo, useCallback } from "react";
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

// --- Node Types/Styles ---

const nodeStyles: Record<string, any> = {
  pattern: "bg-indigo-900 border-indigo-500 text-white font-bold shadow-[0_0_15px_rgba(99,102,241,0.5)]",
  episode: "bg-purple-900 border-purple-500 text-slate-100",
  trigger: "bg-amber-900 border-amber-500 text-amber-100",
  entity: "bg-emerald-900 border-emerald-500 text-emerald-100",
  error: "bg-rose-900 border-rose-500 text-rose-100",
  root_cause: "bg-orange-900 border-orange-500 text-orange-100",
};

const edgeStyles: Record<string, any> = {
  belongs_to: { stroke: "#818cf8", strokeDasharray: "5 5" },
  trigger_of: { stroke: "#fbbf24" },
  involved_in: { stroke: "#34d399" },
  discovered_in: { stroke: "#fb7185" },
  causes: { stroke: "#fb923c" },
};

// --- Dagre Layout ---

const dagreGraph = new dagre.graphlib.Graph();
dagreGraph.setDefaultEdgeLabel(() => ({}));

const getLayoutedElements = (nodes: Node[], edges: Edge[], direction = "LR") => {
  const isHorizontal = direction === "LR";
  dagreGraph.setGraph({ rankdir: direction });

  nodes.forEach((node) => {
    dagreGraph.setNode(node.id, { width: 180, height: 50 });
  });

  edges.forEach((edge) => {
    dagreGraph.setEdge(edge.source, edge.target);
  });

  dagre.layout(dagreGraph);

  return {
    nodes: nodes.map((node) => {
      const nodeWithPosition = dagreGraph.node(node.id);
      return {
        ...node,
        targetPosition: isHorizontal ? Position.Left : Position.Top,
        sourcePosition: isHorizontal ? Position.Right : Position.Bottom,
        position: {
          x: nodeWithPosition.x - 90,
          y: nodeWithPosition.y - 25,
        },
      };
    }),
    edges,
  };
};

// --- Component ---

export function PatternGraph({ patternId }: { patternId: string }) {
  const [nodes, setNodes, onNodesChange] = useNodesState([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);

  const { data, isLoading, error } = useQuery({
    queryKey: ["pattern-graph", patternId],
    queryFn: () => api.get(`/patterns/${patternId}/graph`),
  });

  useEffect(() => {
    if (data && data.nodes) {
      const rawNodes: Node[] = data.nodes.map((n: any) => ({
        id: `${n.type}:${n.id}`,
        data: { label: n.title || n.type.toUpperCase() },
        className: `px-4 py-2 border-2 rounded-lg text-sm transition-all hover:scale-105 ${
          nodeStyles[n.type] || "bg-slate-800 border-slate-600"
        }`,
        type: "default",
      }));

      const rawEdges: Edge[] = data.edges.map((e: any, i: number) => ({
        id: `e-${i}`,
        source: e.source,
        target: e.target,
        label: e.type.replace("_", " "),
        labelStyle: { fill: "#94a3b8", fontSize: "10px", fontWeight: 500 },
        style: edgeStyles[e.type] || { stroke: "#475569" },
        animated: true,
        markerEnd: { type: MarkerType.ArrowClosed, color: (edgeStyles[e.type] || { stroke: "#475569" }).stroke },
      }));

      const { nodes: layoutedNodes, edges: layoutedEdges } = getLayoutedElements(
        rawNodes,
        rawEdges
      );

      setNodes(layoutedNodes);
      setEdges(layoutedEdges);
    }
  }, [data, setNodes, setEdges]);

  if (isLoading) return <div className="flex justify-center p-12"><Loader2 className="animate-spin h-8 w-8 text-indigo-500" /></div>;
  if (error) return <div className="text-destructive p-4 border rounded-xl bg-destructive/10">Failed to load graph</div>;

  return (
    <div className="relative w-full h-[600px] border rounded-xl bg-[#020617] overflow-hidden group">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        fitView
        colorMode="dark"
      >
        <Background color="#1e293b" gap={20} />
        <Controls showInteractive={false} className="bg-slate-900 border-slate-700 fill-slate-200" />
        
        <Panel position="top-left" className="bg-slate-900/90 border border-slate-700 p-3 rounded-lg text-xs space-y-2 backdrop-blur-sm shadow-xl">
           <div className="font-semibold text-slate-400 mb-1 flex items-center gap-1.5"><Network className="h-3 w-3" /> Map Legend</div>
           <div className="grid grid-cols-2 gap-x-4 gap-y-1.5">
              <div className="flex items-center gap-2"><div className="w-2.5 h-2.5 rounded-sm bg-indigo-500" /> Pattern</div>
              <div className="flex items-center gap-2"><div className="w-2.5 h-2.5 rounded-sm bg-purple-500" /> Episode</div>
              <div className="flex items-center gap-2"><div className="w-2.5 h-2.5 rounded-sm bg-amber-500" /> Trigger</div>
              <div className="flex items-center gap-2"><div className="w-2.5 h-2.5 rounded-sm bg-emerald-500" /> Entity</div>
              <div className="flex items-center gap-2"><div className="w-2.5 h-2.5 rounded-sm bg-rose-500" /> Error</div>
              <div className="flex items-center gap-2"><div className="w-2.5 h-2.5 rounded-sm bg-orange-500" /> Cause</div>
           </div>
        </Panel>

        {nodes.length <= 1 && (
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
                  <div className="mt-4 flex justify-center gap-2">
                     <span className="px-2 py-1 bg-indigo-500/10 text-indigo-400 border border-indigo-500/20 rounded text-[10px] font-mono uppercase tracking-wider">Awaiting Context</span>
                  </div>
              </div>
           </div>
        )}
      </ReactFlow>
    </div>
  );
}
