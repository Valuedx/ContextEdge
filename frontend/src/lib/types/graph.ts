export interface GraphNodeRef {
  type: string;
  id: string;
}

export interface GraphNode {
  type: string;
  id: string;
  title?: string | null;
}

export interface GraphEdge {
  source: string;
  target: string;
  type: string;
  weight: number;
}

export interface GraphSubgraphResponse {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export interface GraphNeighbor {
  node_type: string;
  node_id: string;
  edge_type: string;
  weight: number;
  direction: "outgoing" | "incoming";
  depth: number;
}

export interface GraphStatsResponse {
  total_edges: number;
  edge_type_counts: Record<string, number>;
  node_type_counts: Record<string, number>;
}

export interface AgentGraphBudget {
  max_nodes: number;
  max_relationships: number;
  max_depth: number;
  max_characters: number;
}

export interface AgentGraphRequest {
  query: string;
  seeds?: GraphNodeRef[];
  session_id?: string;
  entities?: string[];
  domain_id?: string;
  max_depth?: number;
  budget?: AgentGraphBudget;
  profile?: string;
  as_of?: string;
}

export interface AgentGraphProvenance {
  source_type: string;
  created_at?: string | null;
  updated_at?: string | null;
  current_state: boolean;
}

export interface AgentGraphNode {
  key: string;
  type: string;
  id: string;
  label: string;
  summary?: string | null;
  facts: Record<string, unknown>;
  confidence?: number | null;
  freshness?: number | null;
  relevance: number;
  provenance: AgentGraphProvenance;
}

export interface AgentGraphRelationship {
  source: string;
  target: string;
  type: string;
  direction: "outgoing";
  weight: number;
  confidence?: number | null;
  relevance: number;
  metadata: Record<string, unknown>;
}

export interface AgentGraphUsage {
  nodes: number;
  relationships: number;
  characters: number;
}

export interface AgentGraphSubset {
  schema_version: string;
  profile: string;
  projection_id: string;
  generated_at: string;
  query: string;
  seeds: GraphNodeRef[];
  nodes: AgentGraphNode[];
  relationships: AgentGraphRelationship[];
  budget: AgentGraphBudget;
  usage: AgentGraphUsage;
  truncated: boolean;
  truncation_reasons: string[];
  warnings: string[];
}

export interface GraphScope {
  domainId?: string;
  asOf?: string;
}
