"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import {
  Background,
  Controls,
  Edge,
  MarkerType,
  Node,
  ReactFlow,
  ReactFlowProvider,
  useEdgesState,
  useNodesState,
  useReactFlow,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import {
  AlertTriangle,
  Braces,
  CheckCircle2,
  CircleGauge,
  Loader2,
  Network,
  Search,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Textarea } from "@/components/ui/textarea";
import { graphApi } from "@/lib/graph-api";
import type {
  AgentGraphNode,
  AgentGraphRelationship,
  AgentGraphRequest,
  AgentGraphSubset,
  GraphScope,
} from "@/lib/types/graph";
import {
  edgeColors,
  getNodeClassName,
  MAF_NODE_TYPE_OPTIONS,
} from "./graph-constants";
import { layoutGraph } from "./graph-layout";
import { GraphNodePicker } from "./graph-node-picker";

type Selection =
  | { kind: "node"; value: AgentGraphNode }
  | { kind: "relationship"; value: AgentGraphRelationship };

function clamp(value: string, minimum: number, maximum: number, fallback: number) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.min(maximum, Math.max(minimum, parsed));
}

function AgentFlow({
  nodes,
  edges,
  onNodesChange,
  onEdgesChange,
  onNodeClick,
  onEdgeClick,
}: {
  nodes: Node[];
  edges: Edge[];
  onNodesChange: ReturnType<typeof useNodesState>[2];
  onEdgesChange: ReturnType<typeof useEdgesState>[2];
  onNodeClick: (node: Node) => void;
  onEdgeClick: (edge: Edge) => void;
}) {
  const { fitView } = useReactFlow();
  useEffect(() => {
    if (nodes.length > 0) {
      requestAnimationFrame(() =>
        fitView({ padding: 0.18, duration: 300, maxZoom: 1.1 }),
      );
    }
  }, [fitView, nodes]);

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      onNodesChange={onNodesChange}
      onEdgesChange={onEdgesChange}
      onNodeClick={(_, node) => onNodeClick(node)}
      onEdgeClick={(_, edge) => onEdgeClick(edge)}
      colorMode="dark"
      fitView
    >
      <Background color="#1e293b" gap={20} />
      <Controls
        showInteractive={false}
        className="border-slate-700 bg-slate-900 fill-slate-200"
      />
    </ReactFlow>
  );
}

function Inspector({ selection }: { selection: Selection | null }) {
  if (!selection) {
    return (
      <div className="flex h-full min-h-48 items-center justify-center p-6 text-center text-sm text-muted-foreground">
        Select a node or relationship.
      </div>
    );
  }

  let title: string;
  let summary: string | null | undefined;
  let details: Record<string, unknown>;
  if (selection.kind === "node") {
    const node = selection.value;
    title = node.label;
    summary = node.summary;
    details = {
      confidence: node.confidence,
      freshness: node.freshness,
      relevance: node.relevance,
      ...node.facts,
      provenance: node.provenance,
    };
  } else {
    const relationship = selection.value;
    title = relationship.type.replaceAll("_", " ");
    summary = null;
    details = {
      source: relationship.source,
      target: relationship.target,
      weight: relationship.weight,
      confidence: relationship.confidence,
      relevance: relationship.relevance,
      ...relationship.metadata,
    };
  }

  return (
    <div className="space-y-4 p-4">
      <div>
        <Badge variant="outline">{selection.kind}</Badge>
        <h3 className="mt-2 break-words text-sm font-semibold">
          {title}
        </h3>
        {summary && (
          <p className="mt-2 whitespace-pre-wrap text-xs leading-5 text-muted-foreground">
            {summary}
          </p>
        )}
      </div>
      <dl className="space-y-2">
        {Object.entries(details)
          .filter(([, item]) => item !== null && item !== undefined)
          .map(([key, item]) => (
            <div key={key} className="border-t pt-2">
              <dt className="text-[11px] font-medium uppercase text-muted-foreground">
                {key.replaceAll("_", " ")}
              </dt>
              <dd className="mt-1 break-words text-xs">
                {typeof item === "object" ? JSON.stringify(item) : String(item)}
              </dd>
            </div>
          ))}
      </dl>
    </div>
  );
}

export function AgentContextPreview({ scope }: { scope: GraphScope }) {
  const [query, setQuery] = useState("");
  const [seedType, setSeedType] = useState("session");
  const [seedId, setSeedId] = useState("");
  const [sessionId, setSessionId] = useState("");
  const [entities, setEntities] = useState("");
  const [maxDepth, setMaxDepth] = useState(2);
  const [maxNodes, setMaxNodes] = useState(24);
  const [maxRelationships, setMaxRelationships] = useState(48);
  const [maxCharacters, setMaxCharacters] = useState(12000);
  const [selection, setSelection] = useState<Selection | null>(null);
  const [mobileInspectorOpen, setMobileInspectorOpen] = useState(false);
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);

  const mutation = useMutation<AgentGraphSubset, Error, AgentGraphRequest>({
    mutationFn: graphApi.agentSubset,
  });

  useEffect(() => {
    const result = mutation.data;
    if (!result) {
      setNodes([]);
      setEdges([]);
      return;
    }
    const rawNodes: Node[] = result.nodes.map((node) => ({
      id: node.key,
      data: { label: node.label, agentNode: node },
      className: `h-[58px] w-[190px] overflow-hidden rounded-lg border-2 px-3 py-2 text-xs ${getNodeClassName(node.type)}`,
      position: { x: 0, y: 0 },
    }));
    const rawEdges: Edge[] = result.relationships.map((relationship, index) => {
      const color = edgeColors[relationship.type] ?? { stroke: "#64748b" };
      return {
        id: `agent-edge-${index}`,
        source: relationship.source,
        target: relationship.target,
        label: relationship.type.replaceAll("_", " "),
        data: { relationship },
        labelStyle: { fill: "#94a3b8", fontSize: 10 },
        style: { stroke: color.stroke, strokeDasharray: color.dasharray },
        markerEnd: { type: MarkerType.ArrowClosed, color: color.stroke },
      };
    });
    const arranged = layoutGraph(rawNodes, rawEdges, {
      nodeWidth: 190,
      nodeHeight: 58,
      nodeSeparation: 64,
      rankSeparation: 110,
    });
    setNodes(arranged.nodes);
    setEdges(arranged.edges);
  }, [mutation.data, setEdges, setNodes]);

  const hasInput = Boolean(
    query.trim() || seedId.trim() || sessionId.trim() || entities.trim(),
  );
  const canRun = hasInput;
  const request = useMemo<AgentGraphRequest>(
    () => ({
      query: query.trim(),
      seeds: seedId.trim()
        ? [{ type: seedType, id: seedId.trim() }]
        : undefined,
      session_id: sessionId.trim() || undefined,
      entities: entities
        .split(",")
        .map((item) => item.trim())
        .filter(Boolean),
      domain_id: scope.domainId,
      as_of: scope.asOf,
      max_depth: maxDepth,
      profile: "maf.v1",
      budget: {
        max_nodes: maxNodes,
        max_relationships: maxRelationships,
        max_depth: maxDepth,
        max_characters: maxCharacters,
      },
    }),
    [
      entities,
      maxCharacters,
      maxDepth,
      maxNodes,
      maxRelationships,
      query,
      scope.asOf,
      scope.domainId,
      seedId,
      seedType,
      sessionId,
    ],
  );

  const inspectNode = useCallback((node: Node) => {
    setSelection({ kind: "node", value: node.data.agentNode as AgentGraphNode });
    if (window.matchMedia("(max-width: 1023px)").matches) {
      setMobileInspectorOpen(true);
    }
  }, []);

  const inspectEdge = useCallback((edge: Edge) => {
    setSelection({
      kind: "relationship",
      value: edge.data?.relationship as AgentGraphRelationship,
    });
    if (window.matchMedia("(max-width: 1023px)").matches) {
      setMobileInspectorOpen(true);
    }
  }, []);

  return (
    <div className="space-y-4">
      <div className="border-b pb-4">
        <div className="grid gap-3 lg:grid-cols-[minmax(280px,2fr)_minmax(220px,1fr)_auto]">
          <div className="space-y-1">
            <label className="text-xs text-muted-foreground" htmlFor="agent-query">
              Agent query
            </label>
            <Textarea
              id="agent-query"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Why did the payment workflow fail?"
              className="min-h-20"
            />
          </div>
          <div className="grid gap-2">
            <GraphNodePicker
              className="grid gap-2"
              nodeType={seedType}
              nodeId={seedId}
              nodeTypes={MAF_NODE_TYPE_OPTIONS}
              onNodeTypeChange={setSeedType}
              onNodeIdChange={setSeedId}
            />
            <GraphNodePicker
              className="grid gap-2"
              nodeType="session"
              nodeId={sessionId}
              nodeTypes={["session"]}
              onNodeIdChange={setSessionId}
              showType={false}
            />
            <label className="space-y-1 text-xs text-muted-foreground">
              Entity terms
              <Input
                value={entities}
                onChange={(event) => setEntities(event.target.value)}
                placeholder="workflow, host"
              />
            </label>
          </div>
          <Button
            className="self-end"
            disabled={!canRun || mutation.isPending}
            onClick={() => {
              setSelection(null);
              mutation.mutate(request);
            }}
          >
            {mutation.isPending ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              <Search className="size-4" />
            )}
            Preview
          </Button>
        </div>

        <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
          <label className="space-y-1 text-xs text-muted-foreground">
            Depth
            <Input
              type="number"
              min={1}
              max={3}
              value={maxDepth}
              onChange={(event) =>
                setMaxDepth(clamp(event.target.value, 1, 3, 2))
              }
            />
          </label>
          <label className="space-y-1 text-xs text-muted-foreground">
            Nodes
            <Input
              type="number"
              min={1}
              max={60}
              value={maxNodes}
              onChange={(event) =>
                setMaxNodes(clamp(event.target.value, 1, 60, 24))
              }
            />
          </label>
          <label className="space-y-1 text-xs text-muted-foreground">
            Relationships
            <Input
              type="number"
              min={0}
              max={120}
              value={maxRelationships}
              onChange={(event) =>
                setMaxRelationships(clamp(event.target.value, 0, 120, 48))
              }
            />
          </label>
          <label className="space-y-1 text-xs text-muted-foreground">
            Characters
            <Input
              type="number"
              min={500}
              max={30000}
              step={500}
              value={maxCharacters}
              onChange={(event) =>
                setMaxCharacters(clamp(event.target.value, 500, 30000, 12000))
              }
            />
          </label>
        </div>
      </div>

      {mutation.error && (
        <div className="border border-destructive/40 bg-destructive/10 p-4 text-sm text-destructive">
          {mutation.error.message}
        </div>
      )}

      {mutation.data && (
        <>
          <div className="flex flex-wrap items-center gap-2 text-xs">
            {mutation.isPending && (
              <Badge variant="outline">
                <Loader2 className="mr-1 size-3 animate-spin" />
                Refreshing
              </Badge>
            )}
            <Badge variant="outline">
              <Braces className="mr-1 size-3" />
              {mutation.data.profile} / {mutation.data.schema_version}
            </Badge>
            <Badge variant="secondary">
              <Network className="mr-1 size-3" />
              {mutation.data.usage.nodes}/{mutation.data.budget.max_nodes} nodes
            </Badge>
            <Badge variant="secondary">
              {mutation.data.usage.relationships}/
              {mutation.data.budget.max_relationships} relationships
            </Badge>
            <Badge variant="secondary">
              <CircleGauge className="mr-1 size-3" />
              {mutation.data.usage.characters.toLocaleString()} characters
            </Badge>
            {mutation.data.truncated ? (
              <Badge variant="destructive">
                <AlertTriangle className="mr-1 size-3" />
                Truncated: {mutation.data.truncation_reasons.join(", ")}
              </Badge>
            ) : (
              <Badge variant="outline">
                <CheckCircle2 className="mr-1 size-3 text-emerald-400" />
                Complete
              </Badge>
            )}
          </div>

          {mutation.data.warnings.map((warning) => (
            <div
              key={warning}
              className="border-l-2 border-amber-400 bg-amber-500/5 px-3 py-2 text-xs text-amber-200"
            >
              {warning}
            </div>
          ))}

          {mutation.data.nodes.length === 0 ? (
            <div className="flex min-h-72 items-center justify-center border text-sm text-muted-foreground">
              No authorized context matched this request.
            </div>
          ) : (
            <div className="grid min-h-[620px] overflow-hidden border bg-[#020617] lg:grid-cols-[minmax(0,1fr)_300px]">
              <div className="h-[620px] min-w-0">
                <ReactFlowProvider>
                  <AgentFlow
                    nodes={nodes}
                    edges={edges}
                    onNodesChange={onNodesChange}
                    onEdgesChange={onEdgesChange}
                    onNodeClick={inspectNode}
                    onEdgeClick={inspectEdge}
                  />
                </ReactFlowProvider>
              </div>
              <aside className="hidden overflow-y-auto border-l bg-background/95 lg:block">
                <Inspector selection={selection} />
              </aside>
            </div>
          )}
        </>
      )}

      <Sheet open={mobileInspectorOpen} onOpenChange={setMobileInspectorOpen}>
        <SheetContent side="right" className="overflow-y-auto lg:hidden">
          <SheetHeader>
            <SheetTitle>Graph details</SheetTitle>
            <SheetDescription>Selected agent context record</SheetDescription>
          </SheetHeader>
          <Inspector selection={selection} />
        </SheetContent>
      </Sheet>
    </div>
  );
}
