# Retention and operational events

## Summary

You will understand how **retention** ages out or archives evidence by **memory class**, how **legal hold** blocks deletion, how **tenant** configuration surfaces retention defaults in schemas, and how **operational events** already covered in governance fit into long-term observability of the data lifecycle.

## Business picture

Organizations need to keep incident data long enough to learn from it, but not forever. Retention policies automatically age out old records according to your rules — short-lived items like chat fragments expire in weeks, while high-value evidence such as linked tickets and root-cause analyses stays longer. When regulations or internal investigations require it, **legal holds** freeze specific items so they are never archived regardless of age. The platform classifies evidence into retention tiers automatically, so teams do not need to tag every record by hand. Meanwhile, an append-only event timeline records what the system did (retention job results, session openings, playbook runs), giving auditors a clear answer to "did the scheduled cleanup actually run last night?"

## Technical walkthrough

### Retention service

- `apply_retention_policy` in `retention_service.py` selects `EvidenceItem` rows for a tenant, skips items with `sensitivity_label == "legal_hold"`, classifies each via `classify_evidence_memory_class` in `memory_service`, compares `ingested_at` to cutoffs from `memory_retention_windows(retention_days)`, and sets `relevance_state = "archived"` for expired items. Returns count archived; logs structured summary.

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

- **Archive via state flag vs hard delete** — *Why:* safer recovery and simpler compliance story; hard delete can be a later phase. *Tradeoff:* storage still occupied until vacuum/compaction policies exist.

- **Legal hold as sensitivity label** — *Why:* one column checked uniformly in retention queries. *Tradeoff:* label semantics must not be reused casually for other meanings.

- **Memory class from rules, not user per row** — *Why:* reduces operator burden at ingest time. *Tradeoff:* classification rules must be maintained as product evolves.

- **Retention invocation outside core HTTP** — *Why:* long scans do not belong on request threads. *Tradeoff:* deployments must schedule workers or admin scripts explicitly.

## Code map

| Concern | Module path | Key symbols | When it runs |
| --- | --- | --- | --- |
| Retention | `backend/src/contextedge/services/retention_service.py` | `apply_retention_policy`, `apply_legal_hold` | Scheduled / admin job |
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
