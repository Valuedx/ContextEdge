# Search: hybrid ranking and access

## Summary

You will see how **evidence search** in the admin API uses PostgreSQL **full-text search (FTS)** plus **access policies**, how **semantic** search uses **pgvector**, and how **playbook runtime ranking** blends FTS, embeddings, **graph** proximity, **identity** hints, freshness, and **risk** caps into one score.

## Business picture

Analysts type plain questions — "VPN certificate expired" — and expect back the most relevant tickets, messages, and documents. The platform delivers **ranked, access-controlled results**: not just keyword matches, but conceptually related evidence from across every connected tool.

Behind the scenes, search combines keyword matching with meaning-based similarity, so results surface even when the analyst uses different words than the original source. At the same time, **access policies ensure analysts only see evidence they are authorized to view** — restricted items are silently excluded, never leaked as placeholders or counts.

The same ranking engine powers **playbook matching** for automation and assistants. When the system needs to recommend a runbook, it weighs keyword relevance, conceptual similarity, how connected the playbook is to known infrastructure entities, and how recently the playbook was validated — then filters out procedures above the caller's authorized risk tier. The result: the right playbook surfaces for the right situation, with full transparency into why it ranked where it did.

## Technical walkthrough

### Evidence list/search (`GET /evidence`)

- `api/v1/evidence.py` `search_evidence`: if `query` is provided, it calls `search_evidence_fts` in `pg_fts.py`, which builds a `plainto_tsquery` against `EvidenceItem.search_tsvector`, orders by `ts_rank`, and respects `exclude_policy_ids` from `resolve_excluded_access_policy_ids`.
- Without a query string, it falls back to filtered listing (source, relevance, type, domain) with the same policy exclusion on `access_policy_id`.

- `get_evidence` returns 404 if the item is excluded by policy.

- `update_access_policy` lets `domain_admin` or `knowledge_manager` assign policies via `policy_assignment.assert_policy_assignment`.

### Semantic evidence search

- `search_evidence_semantic` in `vector_search.py` embeds the query (or accepts a precomputed embedding), orders by **cosine distance** against `EvidenceItem.embedding`, and applies the same optional policy filter.

> **Today (May 2026):** vector search reads the parent `evidence_items.embedding` only. Per-chunk embeddings exist (migration `0030_evidence_chunks`, written by `evidence_chunk_service.write_chunks` and embedded by `embed_chunks_batch_task`) but the query-side rollup is not wired yet. See "Chunk-level retrieval (planned)" below.

### Chunk-level retrieval (planned)

The chunking pipeline writes one `EvidenceChunk` row per per-source-meaningful unit (a Jira description, a Teams message, a runbook heading section, a log event). Each chunk has its own 3072-dim embedding, an HNSW index (`ix_evidence_chunks_embedding_hnsw`), and per-chunk metadata (`priority`, `author`, `severity`, `source_authority`, `parent_section`, …). The next change to `vector_search.py` + `hybrid_ranker.py` is the rollup pattern that exploits this:

1. **Hybrid retrieval** — vector top-50 against `evidence_chunks.embedding` **+** BM25 top-50 against `evidence_items.search_tsvector`. Hybrid catches stack traces and command-name queries that pure embeddings miss.
2. **MMR at the chunk level** — before the parent rollup, dedupe near-duplicate chunks so five hits from one long thread don't crowd out four distinct evidences.
3. **Parent rollup** — group chunk hits by `evidence_id`, take the closest chunk per evidence as the parent score, **but keep the `chunk_id`** through the pipeline so the LLM-context path can return the specific chunk with its `parent_section` breadcrumb instead of the entire body.
4. **Per-chunk authority + recency features** — `metadata.source_authority` (`runbook` > `ticket` > `email` > `chat` > `gist`) and parent `created_at_source` feed the re-ranker; this is the day-1 lever for the future re-rank step.

The parent's `embedding` column is preserved unchanged so contradiction scanning, similar-decision retrieval, and baseline matching keep working without modification. The rollup adds a chunk-vector path; it does not replace the parent path.

Detail in [`CHUNKING_DESIGN.md`](./CHUNKING_DESIGN.md) §6 (search integration).

### Playbook hybrid ranking

- `rank_playbooks` in `hybrid_ranker.py` loads **approved** playbooks for the tenant, optionally filters by `domain_id`, `allowed_domain_ids` (service tokens), and `max_risk_tier` using `risk_within_cap` from `risk_policy.py`.
- For each playbook it resolves the **latest published** `PlaybookVersion` (skips if none).
- **Keyword:** `search_playbooks_fts` scores titles/descriptions.
- **Semantic:** `search_evidence_semantic_for_playbook` searches evidence linked to that **published** version.
- **Graph:** `_graph_score_for_playbook` counts `GraphEdge` rows involving the playbook and blends in a **correlation co-occurrence boost** via `CorrelationEdge` — if the playbook's evidence overlaps with semantic search hits, the score increases. Both functions accept an optional `domain_id` so that domain-scoped ranking only considers edges in that domain (plus domain-less edges).
- **Identity:** `_identity_score_for_playbook` counts identity graph edges matching the query's resolved identity IDs. Also accepts `domain_id` for scoped scoring.
- **Negative penalty:** `_negative_penalty_for_playbook` computes a downward adjustment based on the number of `contradicts` graph edges and domain-scoped `NegativeKnowledgeItem` rows, capped at 1.0. This penalizes playbooks associated with contradictions or known-bad guidance.
- Weights default via `RankingWeights` (keyword, semantic, graph, evidence quality, identity, recency, freshness, negative penalty).
- Admins bypass certain access exclusions: `resolve_excluded_access_policy_ids` returns `None` when roles intersect `ADMIN_ROLES`.

## Example: Acme VPN data at this stage

**Input — analyst searches for VPN evidence**

```
GET /api/v1/evidence?query=VPN+certificate+expired&domain_id=vpn-connectivity
Authorization: Bearer <analyst-token>
```

**Output — ranked evidence results (security-trimmed)**

```json
{
  "results": [
    {
      "evidence_id": "ev-a1b2c3",
      "title": "VPN connection drops after Windows update KB5032190",
      "body_summary": "Multiple users report VPN disconnects following patch Tuesday. AUTH_CERT_EXPIRED on vpn-gw-east-01.",
      "relevance_state": "operational",
      "source_type": "jira_sm",
      "fts_rank": 0.87
    },
    {
      "evidence_id": "ev-d4e5f6",
      "title": "Teams thread: VPN outage troubleshooting",
      "body_summary": "Engineers confirmed AUTH_CERT_EXPIRED. Discussed certificate renewal steps.",
      "relevance_state": "operational",
      "source_type": "teams",
      "fts_rank": 0.74
    }
  ],
  "total": 2
}
```

Evidence items with restricted access policies that the analyst's role does not cover are excluded from results — they do not appear at all, not even as placeholders.

**Input — runtime match request for playbook ranking**

```json
{
  "symptoms": ["VPN authentication failure", "users cannot connect"],
  "entities": ["vpn-gw-east-01", "KB5034567"],
  "context": "Post-patch Tuesday, multiple user reports"
}
```

**Output — hybrid-ranked playbook matches**

```json
{
  "matches": [
    {
      "playbook_id": "pb-r1s2t3",
      "title": "VPN Certificate Rotation After Patch Tuesday",
      "confidence": 0.92,
      "breakdown": {
        "keyword": 0.85,
        "semantic": 0.94,
        "graph": 0.88,
        "identity": 0.80,
        "recency": 0.95,
        "freshness": 1.0,
        "negative_penalty": 0.0
      },
      "evidence_trace": ["ev-a1b2c3", "ev-d4e5f6"],
      "freshness": "current",
      "risk_tier": "medium"
    }
  ]
}
```

The `breakdown` field lets reviewers understand why a playbook ranked where it did — for example, high semantic similarity but lower keyword match because the analyst used different terms than the playbook title.

## Design decisions

- **FTS columns + GIN (see migrations)** — *Why:* fast keyword retrieval at scale. *Tradeoff:* tsvector must be maintained on write/update.

- **pgvector for semantics** — *Why:* "similar meaning" queries FTS misses. *Tradeoff:* embedding dimension (3072 in provider path) and index size; provider outages affect semantic leg.

- **Hybrid vs single-signal** — *Why:* operational search is inherently multi-signal. *Tradeoff:* tuning weights is product-specific; explainability uses `breakdown` on `RankedPlaybook`.

- **Policy exclusion at SQL layer** — *Why:* defense in depth vs filtering in Python only. *Tradeoff:* every search query must thread `exclude_policy_ids`.

- **Risk tier cap separate from tenant policy config** — *Why:* runtime caps are role-based today (see technical blueprint). *Tradeoff:* policy JSON evolution may later unify this.

## Code map

| Concern | Module path | Key symbols | When it runs |
| --- | --- | --- | --- |
| Evidence HTTP | `backend/src/contextedge/api/v1/evidence.py` | `search_evidence`, `get_evidence`, `update_access_policy` | API |
| Evidence FTS | `backend/src/contextedge/search/pg_fts.py` | `search_evidence_fts`, `search_playbooks_fts` | Query time |
| Evidence semantic | `backend/src/contextedge/search/vector_search.py` | `search_evidence_semantic`, `search_evidence_semantic_for_playbook` | Query / rank |
| Hybrid ranker | `backend/src/contextedge/search/hybrid_ranker.py` | `rank_playbooks`, `RankingWeights`, `RankedPlaybook`, `_negative_penalty_for_playbook` | Runtime / evaluation |
| Access policies | `backend/src/contextedge/search/access_control.py` | `resolve_excluded_access_policy_ids` | Retrieval |
| Risk ordering | `backend/src/contextedge/search/risk_policy.py` | `risk_within_cap`, `playbook_risk_rank` | Runtime filter |
| Runtime API | `backend/src/contextedge/api/v1/runtime.py` | (calls `rank_playbooks`) | HTTP |
| Runtime service | `backend/src/contextedge/services/runtime_service.py` | `match_playbooks` | Service layer |

## Acme VPN incident (this layer)

A **knowledge manager** searches "VPN gateway"; FTS ranks Acme's normalized tickets; an analyst without clearance for restricted policies sees fewer rows because `access_policy_id` matches excluded tenant policies. Runtime matching for an integration uses the same hybrid function with optional **domain** and **risk** caps so only appropriate VPN playbooks surface.

## Further reading

- [04-evidence-normalization-and-storage.md](./04-evidence-normalization-and-storage.md) — where embeddings land  
- [01-end-to-end-pipeline.md](./01-end-to-end-pipeline.md) — runtime in context  
- [`docs/API.md`](../docs/API.md) — evidence and runtime routes  
