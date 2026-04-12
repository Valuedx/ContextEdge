# Retention and operational events

## Summary

You will understand how **retention** ages out or archives evidence by **memory class**, how **legal hold** blocks deletion, how **tenant** configuration surfaces retention defaults in schemas, and how **operational events** already covered in governance fit into long-term observability of the data lifecycle.

## Business picture

Companies must keep operational history long enough to learn from incidents but not forever—regulators and contracts differ by data type. **Legal hold** freezes items under investigation. The platform classifies evidence into **memory classes** (short vs long term) so retention windows can vary without manual tagging on every row. Separately, **operational events** create an append-only style timeline of “what the system did,” useful when proving retention jobs ran or sessions were opened.

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

After the VPN incident closes, Acme’s default **retention** window archives stale chat fragments but leaves the **legal_hold** email chain untouched while Legal reviews; an `operational_events` row records the nightly retention job counts for auditors.

## Further reading

- [04-evidence-normalization-and-storage.md](./04-evidence-normalization-and-storage.md) — evidence fields retention uses  
- [10-governance-sessions-execution-audit.md](./10-governance-sessions-execution-audit.md) — operational events in depth  
- [`docs/RUNBOOK.md`](../docs/RUNBOOK.md) — scheduling and operations  
