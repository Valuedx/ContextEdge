import { Position, type Edge, type Node } from "@xyflow/react";
import dagre from "dagre";

interface GraphLayoutOptions {
  direction?: "LR" | "TB";
  nodeWidth: number;
  nodeHeight: number;
  nodeSeparation?: number;
  rankSeparation?: number;
}

export function layoutGraph(
  nodes: Node[],
  edges: Edge[],
  {
    direction = "LR",
    nodeWidth,
    nodeHeight,
    nodeSeparation = 60,
    rankSeparation = 100,
  }: GraphLayoutOptions,
) {
  const graph = new dagre.graphlib.Graph();
  graph.setDefaultEdgeLabel(() => ({}));
  graph.setGraph({
    rankdir: direction,
    nodesep: nodeSeparation,
    ranksep: rankSeparation,
  });
  nodes.forEach((node) =>
    graph.setNode(node.id, { width: nodeWidth, height: nodeHeight }),
  );
  edges.forEach((edge) => graph.setEdge(edge.source, edge.target));
  dagre.layout(graph);

  const horizontal = direction === "LR";
  return {
    nodes: nodes.map((node) => {
      const point = graph.node(node.id);
      return {
        ...node,
        sourcePosition: horizontal ? Position.Right : Position.Bottom,
        targetPosition: horizontal ? Position.Left : Position.Top,
        position: {
          x: point.x - nodeWidth / 2,
          y: point.y - nodeHeight / 2,
        },
      };
    }),
    edges,
  };
}
