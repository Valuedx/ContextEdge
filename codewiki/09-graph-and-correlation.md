# Graph and correlation

## Summary

You will learn how **correlation edges** tie evidence items to each other, how the **context graph** (`GraphEdge`) links playbooks to identities and other nodes, and how **contradiction** detection connects approved playbooks to conflicting knowledge—three complementary "relationship" layers.

## Business picture

Incidents rarely live in one ticket. The platform connects related evidence across systems—linking a Jira bug to a Teams conversation to a ServiceNow incident—so teams can see the full picture without manually hunting for context. It builds a **network of relationships** that powers smarter retrieval: when someone searches for help, results are ranked higher if they are well-connected to other relevant evidence. The platform also periodically checks whether **official guidance** (playbooks, KB articles) contradicts what teams are actually seeing in the field, flagging conflicts before outdated or incorrect advice reaches the people who need it most.

## Technical walkthrough

### Correlation service

- `correlation_service.create_correlation` inserts `CorrelationEdge` between two `EvidenceItem` rows with type, confidence, explanation, and creator.
- `get_correlated_evidence` fetches neighbors for an item.
- `extract_case_link_candidates` derives external system/id pairs from `RawEvidenceObject` and thread metadata to support **case linking** (`CaseLink` model) for cross-system traceability.
- Worker `correlate_evidence` triggers correlation logic after normalization (see `extraction_tasks.py`).

### Graph builder

- `graph/builder.py` provides `add_edge`, `ensure_edge` (idempotent), `link_node_to_identities`, `add_contradicts_edge`, and `persist_pattern_enrichment_edges`.
- Edges store `tenant_id`, `domain_id`, `source_node_type`, `source_node_id`, `target_node_type`, `target_node_id`, `edge_type`, `weight`, and `metadata_extra`. The optional `domain_id` scopes edges to a specific domain while still allowing tenant-wide (NULL domain) edges.
- `ensure_edge` and `add_contradicts_edge` use `domain_id` in their idempotency key so that the same logical relationship in different domains produces distinct edges.
- `persist_pattern_enrichment_edges` converts pattern enrichment metadata (triggers, entities, errors, root causes) into persisted `GraphEdge` rows using deterministic UUIDs (`_enrichment_node_id`). This replaces the earlier approach of generating virtual nodes on read, enabling BFS traversal and generic subgraph queries to reach enrichment data without special-case code.
- `hybrid_ranker` uses graph edge **counts** around playbooks as a ranking signal; identity links use types like `references_identity`.

### Contradictions

- `contradiction_service` compares playbook step text to KB-typed evidence (`kb_article`, `sop`, `documentation`), uses token overlap heuristics (`should_compare_contradiction`), then may call `llm_complete_json` for a structured judgment, persists `Contradiction` rows, adds graph edges, emits operational events, and can notify via `notification_service`.
- `scan_contradictions_task` runs tenant-scoped scans on a schedule.

### Graph queries

- `graph/queries.py` provides the read surface for all graph traversal and statistics:
  - `get_neighbors` performs iterative **BFS** up to `max_depth` hops (capped at 3). Each result includes `node_type`, `node_id`, `edge_type`, `weight`, `direction` (outgoing/incoming), and `depth`. When `domain_id` is supplied, only edges matching that domain or domain-less (NULL) edges are followed.
  - `get_pattern_subgraph` returns nodes and edges around a specific pattern, including persisted enrichment edges. Accepts optional `domain_id` filtering.
  - `get_entity_subgraph` is a generic BFS subgraph suitable for any node type, returning `{nodes, edges}` for visualization. Supports `max_depth` and `domain_id`.
  - `get_graph_stats` returns aggregate `total_edges`, `edge_type_counts`, and `node_type_counts` for a tenant. Node counts use a `UNION ALL` of source and target roles with `DISTINCT` to avoid double-counting nodes that appear on both sides of edges.
- Prefer this module when adding new graph read paths for consistency.

### Graph HTTP API

The `/graph` router (`api/v1/graph.py`) exposes three endpoints for generic graph exploration:

| Method | Path | Key params | Returns |
| --- | --- | --- | --- |
| `GET` | `/graph/neighbors` | `node_type`, `node_id`, `edge_type?`, `max_depth?` (1–3), `domain_id?` | Array of neighbor objects with `direction` and `depth` |
| `GET` | `/graph/subgraph/{entity_type}/{entity_id}` | `max_depth?` (1–3), `domain_id?` | `{nodes: [...], edges: [...]}` for React Flow visualization |
| `GET` | `/graph/stats` | `domain_id?` | `{total_edges, edge_type_counts, node_type_counts}` |

All endpoints are tenant-scoped via the authenticated user. When `domain_id` is provided, queries include both domain-specific and domain-less (NULL) edges.

## Example: Acme VPN data at this stage

**Input — two evidence items from different systems**

| Evidence | Source | Summary |
| --- | --- | --- |
| ev-a1b2c3 | Jira (JIRA-4521) | VPN connection drops after Windows update KB5032190 |
| ev-d4e5f6 | Teams (#vpn-support) | Engineers discuss AUTH_CERT_EXPIRED errors on vpn-gw-east-01 |

**Output — correlation edge**

```json
{
  "correlation_id": "corr-001",
  "evidence_a": "ev-a1b2c3",
  "evidence_b": "ev-d4e5f6",
  "correlation_type": "same_case",
  "confidence": 0.85,
  "explanation": "Both reference VPN gateway vpn-gw-east-01, AUTH_CERT_EXPIRED error, and overlap within 2-hour window",
  "created_by": "system"
}
```

**Output — graph edges linking playbook to identities**

```json
[
  {
    "source_node": { "type": "playbook", "id": "pb-r1s2t3" },
    "target_node": { "type": "identity", "id": "id:vpn-gw-east-01" },
    "edge_type": "references_identity",
    "weight": 0.9,
    "domain_id": "vpn-connectivity"
  },
  {
    "source_node": { "type": "playbook", "id": "pb-r1s2t3" },
    "target_node": { "type": "identity", "id": "id:kb5032190" },
    "edge_type": "references_identity",
    "weight": 0.85,
    "domain_id": "vpn-connectivity"
  }
]
```

**Output — contradiction detected by scheduled scan**

```json
{
  "contradiction_id": "contra-001",
  "playbook_id": "pb-r1s2t3",
  "conflicting_evidence_id": "ev-kb-old-vpn",
  "description": "KB article 'VPN Troubleshooting Guide v2.1' still recommends disabling MFA for VPN, which conflicts with the approved playbook's certificate rotation approach",
  "severity": "medium",
  "detected_at": "2026-03-22T02:00:00Z"
}
```

Contradictions prompt the knowledge manager to either update the KB article or revise the playbook, preventing teams from following outdated guidance.

## Design decisions

- **Adjacency in Postgres vs dedicated graph DB** — *Why:* simpler ops, transactional consistency with evidence and playbooks. *Tradeoff:* deep graph algorithms are harder than in Neo4j-style stores.

- **Separate correlation edges from graph edges** — *Why:* correlation is evidence-centric and user-facing; graph supports many node types for ranking and memory. *Tradeoff:* two concepts to explain to implementers.

- **LLM-assisted contradiction with lexical gate** — *Why:* reduce false expensive LLM calls. *Tradeoff:* rare contradictions with vocabulary mismatch might be missed.

- **Enrichment data persisted as real edges vs virtual nodes** — *Why:* BFS traversal, generic subgraph queries, and the Graph Explorer all reach enrichment data (triggers, entities, errors, root causes) without special-case code. *Tradeoff:* more rows in `graph_edges`; deterministic UUID generation via `uuid5` keeps edge creation idempotent.

- **`domain_id` on graph edges** — *Why:* enables domain-scoped views of the context graph (e.g. only VPN-related edges) while keeping tenant-wide edges accessible. *Tradeoff:* all builder call sites must propagate `domain_id`; queries use `OR domain_id IS NULL` to include unscoped edges.

## Code map

| Concern | Module path | Key symbols | When it runs |
| --- | --- | --- | --- |
| Evidence correlation | `backend/src/contextedge/services/correlation_service.py` | `create_correlation`, `get_correlated_evidence`, `extract_case_link_candidates` | APIs / workers |
| Graph mutations | `backend/src/contextedge/graph/builder.py` | `add_edge`, `ensure_edge`, `link_node_to_identities`, `add_contradicts_edge`, `persist_pattern_enrichment_edges`, `_enrichment_node_id` | Promotion / contradictions / pattern discovery |
| Graph reads | `backend/src/contextedge/graph/queries.py` | `get_neighbors`, `get_pattern_subgraph`, `get_entity_subgraph`, `get_graph_stats` | Services / API |
| Graph HTTP API | `backend/src/contextedge/api/v1/graph.py` | `graph_neighbors`, `graph_subgraph`, `graph_stats` | HTTP |
| Contradictions | `backend/src/contextedge/services/contradiction_service.py` | `should_compare_contradiction`, scan helpers | Evaluation task |
| Correlation worker | `backend/src/contextedge/workers/correlation_tasks.py` | `correlate_evidence` | extraction queue |
| Hybrid use of graph | `backend/src/contextedge/search/hybrid_ranker.py` | `_graph_score_for_playbook`, `_identity_score_for_playbook`, `_negative_penalty_for_playbook` | Runtime ranking |
| Models | `backend/src/contextedge/models/pattern.py` | `GraphEdge` (incl. `domain_id`), `Contradiction` | ORM |
| Episode correlation model | `backend/src/contextedge/models/episode.py` | `CorrelationEdge` | ORM |

## Acme VPN incident (this layer)

`correlate_evidence` links the ServiceNow incident to the Jira clone and the engineer's email; `GraphEdge` ties the approved **VPN playbook** to **identity** nodes for gateway hosts; a scheduled contradiction scan flags an outdated KB article that still says "disable MFA for VPN"—prompting Acme to update KB or playbook text.

## Further reading

- [05-search-hybrid-and-access.md](./05-search-hybrid-and-access.md) — graph signals in ranking (including `domain_id`-scoped scoring and negative penalty)  
- [07-episodes-patterns-playbooks.md](./07-episodes-patterns-playbooks.md) — approved playbook content  
- [08-workers-celery-queues.md](./08-workers-celery-queues.md) — `scan_contradictions_task`  
- [12-identity-resolution-and-thread-hydration.md](./12-identity-resolution-and-thread-hydration.md) — `link_node_to_identities` and identity graph edges  
- [15-dashboard-and-operator-workflows.md](./15-dashboard-and-operator-workflows.md) — Graph Explorer UI for interactive graph visualization  
- [`docs/API.md`](../docs/API.md) — `/graph` endpoint catalog  
