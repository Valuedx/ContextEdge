# Graph and correlation

## Summary

You will learn how **correlation edges** tie evidence items to each other, how the **context graph** (`GraphEdge`) links playbooks to identities and other nodes, and how **contradiction** detection connects approved playbooks to conflicting knowledge—three complementary “relationship” layers.

## Business picture

Incidents rarely live in one ticket. Analysts need “show me everything related.” The platform records **pairwise correlations** between evidence (possibly cross-source), builds a broader **graph** for retrieval scoring and exploration, and periodically asks whether **official playbooks** disagree with **knowledge-base** articles—so governance can resolve conflicts before bad advice spreads.

## Technical walkthrough

### Correlation service

- `correlation_service.create_correlation` inserts `CorrelationEdge` between two `EvidenceItem` rows with type, confidence, explanation, and creator.
- `get_correlated_evidence` fetches neighbors for an item.
- `extract_case_link_candidates` derives external system/id pairs from `RawEvidenceObject` and thread metadata to support **case linking** (`CaseLink` model) for cross-system traceability.
- Worker `correlate_evidence` triggers correlation logic after normalization (see `extraction_tasks.py`).

### Graph builder

- `graph/builder.py` provides `add_edge`, `ensure_edge` (idempotent), `link_node_to_identities`, and specialized helpers such as `add_contradicts_edge`.
- Edges store `source_node_type`, `source_node_id`, `target_node_type`, `target_node_id`, `edge_type`, `weight`, and `metadata_extra`.
- `hybrid_ranker` uses graph edge **counts** around playbooks as a ranking signal; identity links use types like `references_identity`.

### Contradictions

- `contradiction_service` compares playbook step text to KB-typed evidence (`kb_article`, `sop`, `documentation`), uses token overlap heuristics (`should_compare_contradiction`), then may call `llm_complete_json` for a structured judgment, persists `Contradiction` rows, adds graph edges, emits operational events, and can notify via `notification_service`.
- `scan_contradictions_task` runs tenant-scoped scans on a schedule.

### Graph queries

- `graph/queries.py` (read helpers) supports traversals and lookups used by services—prefer this module when adding new graph read paths for consistency.

## Design decisions

- **Adjacency in Postgres vs dedicated graph DB** — *Why:* simpler ops, transactional consistency with evidence and playbooks. *Tradeoff:* deep graph algorithms are harder than in Neo4j-style stores.

- **Separate correlation edges from graph edges** — *Why:* correlation is evidence-centric and user-facing; graph supports many node types for ranking and memory. *Tradeoff:* two concepts to explain to implementers.

- **LLM-assisted contradiction with lexical gate** — *Why:* reduce false expensive LLM calls. *Tradeoff:* rare contradictions with vocabulary mismatch might be missed.

## Code map

| Concern | Module path | Key symbols | When it runs |
| --- | --- | --- | --- |
| Evidence correlation | `backend/src/contextedge/services/correlation_service.py` | `create_correlation`, `get_correlated_evidence`, `extract_case_link_candidates` | APIs / workers |
| Graph mutations | `backend/src/contextedge/graph/builder.py` | `add_edge`, `ensure_edge`, `link_node_to_identities`, `add_contradicts_edge` | Promotion / contradictions |
| Graph reads | `backend/src/contextedge/graph/queries.py` | `get_neighbors`, related traversals | Services |
| Contradictions | `backend/src/contextedge/services/contradiction_service.py` | `should_compare_contradiction`, scan helpers | Evaluation task |
| Correlation worker | `backend/src/contextedge/workers/correlation_tasks.py` | `correlate_evidence` | extraction queue |
| Hybrid use of graph | `backend/src/contextedge/search/hybrid_ranker.py` | `_graph_score_for_playbook`, `_identity_score_for_playbook` | Runtime ranking |
| Models | `backend/src/contextedge/models/pattern.py` | `GraphEdge`, `Contradiction` | ORM |
| Episode correlation model | `backend/src/contextedge/models/episode.py` | `CorrelationEdge` | ORM |

## Acme VPN incident (this layer)

`correlate_evidence` links the ServiceNow incident to the Jira clone and the engineer’s email; `GraphEdge` ties the approved **VPN playbook** to **identity** nodes for gateway hosts; a scheduled contradiction scan flags an outdated KB article that still says “disable MFA for VPN”—prompting Acme to update KB or playbook text.

## Further reading

- [05-search-hybrid-and-access.md](./05-search-hybrid-and-access.md) — graph signals in ranking  
- [07-episodes-patterns-playbooks.md](./07-episodes-patterns-playbooks.md) — approved playbook content  
- [08-workers-celery-queues.md](./08-workers-celery-queues.md) — `scan_contradictions_task`  
- [12-identity-resolution-and-thread-hydration.md](./12-identity-resolution-and-thread-hydration.md) — `link_node_to_identities` and identity graph edges  
