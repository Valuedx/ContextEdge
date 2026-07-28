import { api } from "@/lib/api";
import type {
  AgentGraphRequest,
  AgentGraphSubset,
  GraphNeighbor,
  GraphScope,
  GraphStatsResponse,
  GraphSubgraphResponse,
} from "@/lib/types/graph";

function scopeParams(scope: GraphScope): Record<string, string> {
  const params: Record<string, string> = {};
  if (scope.domainId) params.domain_id = scope.domainId;
  if (scope.asOf) params.as_of = scope.asOf;
  return params;
}

export const graphApi = {
  stats(scope: GraphScope) {
    return api.get<GraphStatsResponse>("/graph/stats", scopeParams(scope));
  },

  subgraph(type: string, id: string, depth: number, scope: GraphScope) {
    return api.get<GraphSubgraphResponse>(`/graph/subgraph/${type}/${id}`, {
      ...scopeParams(scope),
      max_depth: String(depth),
    });
  },

  neighbors(
    nodeType: string,
    nodeId: string,
    edgeType: string,
    depth: number,
    scope: GraphScope,
  ) {
    const params: Record<string, string> = {
      ...scopeParams(scope),
      node_type: nodeType,
      node_id: nodeId,
      max_depth: String(depth),
    };
    if (edgeType) params.edge_type = edgeType;
    return api.get<GraphNeighbor[]>("/graph/neighbors", params);
  },

  agentSubset(request: AgentGraphRequest) {
    return api.post<AgentGraphSubset>("/graph/agent-subsets", request);
  },
};
