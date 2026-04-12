# Search: hybrid ranking and access

## Summary

You will see how **evidence search** in the admin API uses PostgreSQL **full-text search (FTS)** plus **access policies**, how **semantic** search uses **pgvector**, and how **playbook runtime ranking** blends FTS, embeddings, **graph** proximity, **identity** hints, freshness, and **risk** caps into one score.

## Business picture

Analysts type natural language: “VPN certificate expired.” The product should return relevant tickets and messages, but **not** evidence the user’s role is not allowed to see. When matching **approved playbooks** for automation or assistants, the same ideas apply: keyword hits matter, but so does **conceptual similarity**, recency, and how connected a playbook is to known entities—without surfacing **high-risk** procedures to callers who should only see safer tiers.

## Technical walkthrough

### Evidence list/search (`GET /evidence`)

- `api/v1/evidence.py` `search_evidence`: if `query` is provided, it calls `search_evidence_fts` in `pg_fts.py`, which builds a `plainto_tsquery` against `EvidenceItem.search_tsvector`, orders by `ts_rank`, and respects `exclude_policy_ids` from `resolve_excluded_access_policy_ids`.
- Without a query string, it falls back to filtered listing (source, relevance, type, domain) with the same policy exclusion on `access_policy_id`.

- `get_evidence` returns 404 if the item is excluded by policy.

- `update_access_policy` lets `domain_admin` or `knowledge_manager` assign policies via `policy_assignment.assert_policy_assignment`.

### Semantic evidence search

- `search_evidence_semantic` in `vector_search.py` embeds the query (or accepts a precomputed embedding), orders by **cosine distance** against `EvidenceItem.embedding`, and applies the same optional policy filter.

### Playbook hybrid ranking

- `rank_playbooks` in `hybrid_ranker.py` loads **approved** playbooks for the tenant, optionally filters by `domain_id`, `allowed_domain_ids` (service tokens), and `max_risk_tier` using `risk_within_cap` from `risk_policy.py`.
- For each playbook it resolves the **latest published** `PlaybookVersion` (skips if none).
- **Keyword:** `search_playbooks_fts` scores titles/descriptions.
- **Semantic:** `search_evidence_semantic_for_playbook` searches evidence linked to that **published** version.
- **Graph:** `_graph_score_for_playbook` counts `GraphEdge` rows involving the playbook.
- **Identity:** `_identity_score_for_playbook` when entity terms resolve to identity IDs via `identity_service.resolve_identity_ids_for_terms`.
- Weights default via `RankingWeights` (keyword, semantic, graph, evidence quality, identity, recency, freshness, negative penalty).
- Admins bypass certain access exclusions: `resolve_excluded_access_policy_ids` returns `None` when roles intersect `ADMIN_ROLES`.

## Design decisions

- **FTS columns + GIN (see migrations)** — *Why:* fast keyword retrieval at scale. *Tradeoff:* tsvector must be maintained on write/update.

- **pgvector for semantics** — *Why:* “similar meaning” queries FTS misses. *Tradeoff:* embedding dimension (3072 in provider path) and index size; provider outages affect semantic leg.

- **Hybrid vs single-signal** — *Why:* operational search is inherently multi-signal. *Tradeoff:* tuning weights is product-specific; explainability uses `breakdown` on `RankedPlaybook`.

- **Policy exclusion at SQL layer** — *Why:* defense in depth vs filtering in Python only. *Tradeoff:* every search query must thread `exclude_policy_ids`.

- **Risk tier cap separate from tenant policy config** — *Why:* runtime caps are role-based today (see technical blueprint). *Tradeoff:* policy JSON evolution may later unify this.

## Code map

| Concern | Module path | Key symbols | When it runs |
| --- | --- | --- | --- |
| Evidence HTTP | `backend/src/contextedge/api/v1/evidence.py` | `search_evidence`, `get_evidence`, `update_access_policy` | API |
| Evidence FTS | `backend/src/contextedge/search/pg_fts.py` | `search_evidence_fts`, `search_playbooks_fts` | Query time |
| Evidence semantic | `backend/src/contextedge/search/vector_search.py` | `search_evidence_semantic`, `search_evidence_semantic_for_playbook` | Query / rank |
| Hybrid ranker | `backend/src/contextedge/search/hybrid_ranker.py` | `rank_playbooks`, `RankingWeights`, `RankedPlaybook` | Runtime / evaluation |
| Access policies | `backend/src/contextedge/search/access_control.py` | `resolve_excluded_access_policy_ids` | Retrieval |
| Risk ordering | `backend/src/contextedge/search/risk_policy.py` | `risk_within_cap`, `playbook_risk_rank` | Runtime filter |
| Runtime API | `backend/src/contextedge/api/v1/runtime.py` | (calls `rank_playbooks`) | HTTP |
| Runtime service | `backend/src/contextedge/services/runtime_service.py` | `match_playbooks` | Service layer |

## Acme VPN incident (this layer)

A **knowledge manager** searches “VPN gateway”; FTS ranks Acme’s normalized tickets; an analyst without clearance for restricted policies sees fewer rows because `access_policy_id` matches excluded tenant policies. Runtime matching for an integration uses the same hybrid function with optional **domain** and **risk** caps so only appropriate VPN playbooks surface.

## Further reading

- [04-evidence-normalization-and-storage.md](./04-evidence-normalization-and-storage.md) — where embeddings land  
- [01-end-to-end-pipeline.md](./01-end-to-end-pipeline.md) — runtime in context  
- [`docs/API.md`](../docs/API.md) — evidence and runtime routes  
