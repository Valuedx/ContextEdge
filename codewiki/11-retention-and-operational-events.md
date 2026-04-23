# Retention and operational events

## Summary

You will understand how **retention** ages out or archives evidence by **memory class**, how **legal hold** blocks deletion, how **tenant** configuration surfaces retention defaults in schemas, and how **operational events** already covered in governance fit into long-term observability of the data lifecycle.

## Business picture

Organizations need to keep incident data long enough to learn from it, but not forever. Retention policies automatically age out old records according to your rules — short-lived items like chat fragments expire in weeks, while high-value evidence such as linked tickets and root-cause analyses stays longer. When regulations or internal investigations require it, **legal holds** freeze specific items so they are never archived regardless of age. The platform classifies evidence into retention tiers automatically, so teams do not need to tag every record by hand. Meanwhile, an append-only event timeline records what the system did (retention job results, session openings, playbook runs), giving auditors a clear answer to "did the scheduled cleanup actually run last night?"

## Technical walkthrough

### Retention service

Retention now happens in **two phases**:

**Phase 1 — archive.** `apply_retention_policy` in `retention_service.py` selects `EvidenceItem` rows for a tenant, skips items with `sensitivity_label == "legal_hold"`, classifies each via `classify_evidence_memory_class` in `memory_service`, compares `ingested_at` to cutoffs from `memory_retention_windows(retention_days)`, and sets `relevance_state = "archived"` for expired items. Archived rows stay in the database and remain searchable (they still have embeddings). Returns count archived; logs structured summary.

**Phase 2 — purge.** `purge_archived_evidence` removes or scrubs rows that have been archived long enough to pass the `archive_grace_days` window (default `DEFAULT_ARCHIVE_GRACE_DAYS = 30`). Two modes:

- `hard_delete` — issues a real `DELETE` against the row. Cascades via FK to `attachment_artifacts`, `correlation_edges`, `contradiction_scan_state` (all ON DELETE CASCADE, aligned on older DBs by migration `0028_orm_ddl_drift_alignment`); `playbook_evidence_links.evidence_id` flips to NULL via the SET-NULL FK added in migration `0027`. Two classes of orphan remain and are reaped by the daily `evaluation.cleanup_hard_deleted_evidence` Beat task (see `workers/cleanup_tasks.py`): MinIO raw blobs whose `RawEvidenceObject` is no longer referenced, and `graph_edges` rows that still point at a deleted evidence id. Use for GDPR right-to-erasure.
- `soft_purge` — NULLs `embedding`, `body_text`, `body_summary`, `canonical_entity_refs` (contains extracted person / service names — real PII), and `raw_object_ref` (so the S3 blob can be lifecycle-reaped and no re-ingest can rehydrate the body); replaces `title` with `"[purged]"`. Row stays for audit / reference linking but content is unrecoverable and similarity search no longer matches. Use when the customer wants content removed but IDs / links preserved.

Both modes honour legal hold in the SQL `WHERE` clause, not via post-filtering. `dry_run=True` returns the candidate count without mutation (powers the admin cost-dashboard pre-purge preview). The `limit` parameter (default 1000) caps rows touched per invocation so a single cron tick doesn't churn millions of rows; `limit_reached` in the return dict signals the caller that another tick will find more work.

**Helpers:**

- `apply_legal_hold` sets `sensitivity_label = "legal_hold"` for given evidence ids.
- Optional `source_class` filter narrows by `evidence_type`.

### Memory classification

- `memory_service` defines constants such as `SHORT_TERM_MEMORY`, `LONG_TERM_MEMORY`, `REASONING_MEMORY`, and helpers used by retention and by session event payloads (`session.created` stores memory class hints). Classification logic examines evidence fields (type, relevance, pattern links—see implementation for current rules).

### Tenant configuration

- `schemas/tenant.py` includes `retention_defaults` on `TenantCreate` / `TenantUpdate` so operators can store per-tenant defaults at provisioning time; actual enforcement still flows through services and scheduled jobs you wire (cron calling `apply_retention_policy` is an operational pattern—see runbook for how your deployment triggers it).

### Operational events (recap)

- `event_log_service.append_operational_event` persists rows to `operational_events` with `entity_type`, `event_type`, optional `session_id`, correlation/causation UUIDs, actor, JSON `payload`, and timestamps (`models/events.py`).
- Use this stream to audit retention runs if you append events from the job that calls `apply_retention_policy`.

## Example: Acme VPN data at this stage

**Input — Acme's retention policy configuration**

```json
{
  "tenant_id": "acme-corp",
  "retention_defaults": {
    "short_term_days": 90,
    "long_term_days": 365,
    "reasoning_memory_days": 180
  }
}
```

**Processing — nightly retention job runs**

The system classifies each evidence item into a memory class based on its type, relevance, and pattern linkages:

| Evidence | Memory class | Age (days) | Retention window | Action |
| --- | --- | --- | --- | --- |
| ev-a1b2c3 (Jira ticket, linked to pattern) | long_term | 45 | 365 days | Keep |
| ev-d4e5f6 (Teams thread, linked to episode) | long_term | 45 | 365 days | Keep |
| ev-old-chat-01 (Teams message, no pattern link) | short_term | 120 | 90 days | Archive |
| ev-legal-hold-01 (Email, legal hold) | long_term | 400 | 365 days | **Skipped** (legal hold) |

**Output — retention job result**

```json
{
  "tenant_id": "acme-corp",
  "items_scanned": 1247,
  "items_archived": 83,
  "items_skipped_legal_hold": 4,
  "items_retained": 1160,
  "run_at": "2026-04-01T03:00:00Z"
}
```

**Legal hold example**

```json
{
  "action": "apply_legal_hold",
  "evidence_ids": ["ev-legal-hold-01", "ev-legal-hold-02"],
  "reason": "Pending investigation by Legal — VPN outage liability review",
  "applied_by": "legal-admin@acme.com",
  "result": "sensitivity_label set to 'legal_hold' — these items are now exempt from retention archival"
}
```

Archived items have their `relevance_state` set to `archived` but remain recoverable for audit and reprocessing. Legal-hold items are never archived regardless of their age.

## Design decisions

- **Two-phase archive → purge** — *Why:* decouples "stop surfacing this" (cheap, reversible) from "remove it for compliance" (irreversible, regulated). The grace window between phases gives operators a recovery aperture and a dry-run preview before any real delete. *Tradeoff:* two jobs to schedule instead of one; a misconfigured grace window can delete more than intended.

- **`updated_at` as archived-at proxy** — *Why:* avoids a new column on a hot table; `updated_at` is bumped whenever `relevance_state` flips to `"archived"`, so day-accurate grace is fine with the 30-day default. *Tradeoff:* minute-accurate compliance would require an explicit `archived_at` column.

- **Hard-delete cascades vs daily cleanup** — *Why:* foreign-key cascades handle the common tables (`attachment_artifacts`, `correlation_edges`, `contradiction_scan_state`); `playbook_evidence_links.evidence_id` uses `SET NULL` so the citation record survives as an audit trail; `graph_edges` and MinIO blobs are reaped asynchronously by the `evaluation.cleanup_hard_deleted_evidence` Beat task. *Tradeoff:* between hard-delete and the next cleanup tick (up to ~24 h), the graph may hold references to missing evidence ids and S3 storage retains the raw blob.

- **Soft-purge preserves the row** — *Why:* some customers need content gone but refuse to break reference-link integrity (incident postmortems, existing playbook citations). NULLing content while keeping the id serves that use case. *Tradeoff:* more retention modes = more policy decisions per tenant.

- **Legal hold as sensitivity label** — *Why:* one column checked uniformly in retention queries. *Tradeoff:* label semantics must not be reused casually for other meanings.

- **Memory class from rules, not user per row** — *Why:* reduces operator burden at ingest time. *Tradeoff:* classification rules must be maintained as product evolves.

- **Retention invocation outside core HTTP** — *Why:* long scans do not belong on request threads. *Tradeoff:* deployments must schedule workers or admin scripts explicitly.

## Code map

| Concern | Module path | Key symbols | When it runs |
| --- | --- | --- | --- |
| Retention | `backend/src/contextedge/services/retention_service.py` | `apply_retention_policy`, `apply_legal_hold`, `purge_archived_evidence`, `DEFAULT_ARCHIVE_GRACE_DAYS`, `PurgeMode` | Scheduled / admin job |
| Memory classes | `backend/src/contextedge/services/memory_service.py` | `classify_evidence_memory_class`, `memory_retention_windows`, `LONG_TERM_MEMORY`, … | Retention / events |
| Evidence model | `backend/src/contextedge/models/evidence.py` | `EvidenceItem` (`relevance_state`, `sensitivity_label`, `ingested_at`) | ORM |
| Tenant schemas | `backend/src/contextedge/schemas/tenant.py` | `TenantCreate`, `TenantUpdate` (`retention_defaults`) | API validation |
| Operational events | `backend/src/contextedge/services/event_log_service.py` | `append_operational_event`, `list_operational_events` | Throughout |
| Event model | `backend/src/contextedge/models/events.py` | `OperationalEvent` | ORM |

## Acme VPN incident (this layer)

After the VPN incident closes, Acme's default **retention** window archives stale chat fragments but leaves the **legal_hold** email chain untouched while Legal reviews; an `operational_events` row records the nightly retention job counts for auditors.

## Further reading

- [04-evidence-normalization-and-storage.md](./04-evidence-normalization-and-storage.md) — evidence fields retention uses  
- [10-governance-sessions-execution-audit.md](./10-governance-sessions-execution-audit.md) — operational events in depth  
- [`docs/RUNBOOK.md`](../docs/RUNBOOK.md) — scheduling and operations  
