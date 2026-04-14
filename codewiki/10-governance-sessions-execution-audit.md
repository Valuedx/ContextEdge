# Governance: sessions, execution, and audit

## Summary

You will see how **resolution sessions** capture an incident's context and **decision trace** events, how **playbook execution** enforces **safety classes** and publication rules, and how **audit logs** and **operational events** provide complementary trails for compliance and debugging.

## Business picture

When teams resolve outages, the platform records **what was known** (symptoms, related tickets), **what was recommended** (matched playbooks and confidence scores), and **what was decided** (approvals, overrides, escalations) — creating an auditable trail that can be reviewed days or months later. When automation runs a playbook, safety controls prevent destructive actions unless the right people have approved them. Every sensitive change is attributable to a specific person and searchable after the fact, so compliance teams can answer "who did what and why" without chasing down chat logs.

## Technical walkthrough

### Resolution sessions

- `session_service.create_resolution_session` inserts `ResolutionSession` with symptoms, entities, external case ids, optional domain, and `append_operational_event` records `session.created` with memory class metadata.
- `append_trace_event` appends `DecisionTraceEvent` rows (inputs, outputs, reasoning, confidence) for AI or runtime steps tied to the session.
- `get_resolution_session` loads trace events via `selectinload`.
- HTTP: `api/v1/sessions.py`.

### Execution

- `execution_service.start_execution` loads the playbook, enforces `lifecycle_state == "approved"`, resolves a **published** `PlaybookVersion` (explicit id or latest published), computes caller **max safety class** from roles + playbook `automation_mode` vs `requested_max_safety_class`, and creates `ExecutionRun` plus related structures; integrates with `append_trace_event` and `append_operational_event` for traceability.
- `ExecutionPolicyError` surfaces policy violations to API as errors.
- Models in `models/execution.py` include `ExecutionRun`, `ExecutionStepRun`, `ToolInvocation`, `ApprovalRequest`, and ordered `SAFETY_CLASSES`.
- HTTP: `api/v1/execution.py`.

### Audit (two channels)

- **HTTP middleware audit** — `RequestAuditMiddleware` writes to `audit_logs` for mutating `/api/v1` calls when tenant is known (sync engine).
- **Explicit async audit** — `log_audit_event` writes `AuditLog` rows with merged request/correlation/causation IDs from context vars.
- **Operational events** — `event_log_service.append_operational_event` records `OperationalEvent` rows (entity type/id, session, correlation/causation, payload) for domain lifecycle narration; `list_operational_events` queries them.

### Runtime explain cache

- Runtime responses may cache explain payloads in **Redis** (see technical blueprint); session and execution flows align with that retrieval story for "why we suggested this playbook."

## Example: Acme VPN data at this stage

**Input — responder opens a resolution session**

```json
{
  "tenant_id": "acme-corp",
  "symptoms": ["VPN authentication failure", "users cannot connect to corporate network"],
  "entities": ["vpn-gw-east-01"],
  "external_case_ids": ["JIRA-4521"],
  "domain_id": "vpn-connectivity"
}
```

**Output — resolution session with decision trace**

```json
{
  "session_id": "sess-abc123",
  "status": "active",
  "created_at": "2026-03-15T10:30:00Z",
  "trace_events": [
    {
      "event_type": "runtime_match",
      "inputs": { "symptoms": ["VPN authentication failure"] },
      "outputs": { "top_match": "pb-r1s2t3", "confidence": 0.92 },
      "reasoning": "Hybrid ranking matched VPN Certificate Rotation playbook based on keyword, semantic, and graph signals",
      "timestamp": "2026-03-15T10:30:05Z"
    },
    {
      "event_type": "human_decision",
      "inputs": { "playbook_id": "pb-r1s2t3", "step": "Renew gateway certificate" },
      "outputs": { "decision": "approved", "decided_by": "admin@acme.com" },
      "timestamp": "2026-03-15T10:35:00Z"
    }
  ]
}
```

**Input — start execution on the approved playbook**

```json
{
  "playbook_id": "pb-r1s2t3",
  "session_id": "sess-abc123",
  "requested_max_safety_class": "low_side_effect"
}
```

**Output — execution run with safety enforcement**

```json
{
  "execution_run_id": "exec-def456",
  "playbook_version": "1.0.0",
  "caller_max_safety_class": "low_side_effect",
  "status": "running",
  "steps": [
    { "step": 1, "action": "Confirm AUTH_CERT_EXPIRED error", "safety_class": "read_only", "status": "completed" },
    { "step": 2, "action": "Check certificate expiry date", "safety_class": "read_only", "status": "completed" },
    { "step": 3, "action": "Renew certificate via internal CA", "safety_class": "medium_side_effect", "status": "blocked_needs_approval" }
  ]
}
```

Step 3 requires `medium_side_effect` clearance, which exceeds the caller's `low_side_effect` cap. An approval request is created for a domain admin before the step can proceed.

## Design decisions

- **Sessions vs execution runs** — *Why:* a session is investigative narrative; execution is governed action with safety caps. *Tradeoff:* operators must link them mentally when both exist for one incident.

- **Safety class derived from roles + automation mode** — *Why:* prevents "run destructive playbook with read-only integration token." *Tradeoff:* role matrix must stay documented for customers.

- **Published version only for execution** — *Why:* same bar as runtime retrieval—no draft automation. *Tradeoff:* testers need published versions or sandboxes.

- **Operational events + audit logs** — *Why:* different query patterns (domain timeline vs compliance export). *Tradeoff:* implementers choose the right sink per change.

## Code map

| Concern | Module path | Key symbols | When it runs |
| --- | --- | --- | --- |
| Sessions | `backend/src/contextedge/services/session_service.py` | `create_resolution_session`, `append_trace_event`, `get_resolution_session` | API / runtime |
| Session model | `backend/src/contextedge/models/session.py` | `ResolutionSession`, `DecisionTraceEvent`, `CaseLink` | ORM |
| Sessions API | `backend/src/contextedge/api/v1/sessions.py` | (handlers) | HTTP |
| Execution | `backend/src/contextedge/services/execution_service.py` | `start_execution`, `ExecutionPolicyError`, `_caller_max_safety_class` | API |
| Execution models | `backend/src/contextedge/models/execution.py` | `ExecutionRun`, `SAFETY_CLASSES`, `ToolInvocation` | ORM |
| Execution API | `backend/src/contextedge/api/v1/execution.py` | (handlers) | HTTP |
| Async audit | `backend/src/contextedge/middleware/audit.py` | `log_audit_event` | Mutations |
| HTTP audit rows | `backend/src/contextedge/middleware/request_audit.py` | `RequestAuditMiddleware` | Mutations |
| Operational events | `backend/src/contextedge/services/event_log_service.py` | `append_operational_event`, `list_operational_events` | Lifecycle |
| Event model | `backend/src/contextedge/models/events.py` | `OperationalEvent` | ORM |
| Audit API | `backend/src/contextedge/api/v1/audit.py` | (log listing) | HTTP |

## Acme VPN incident (this layer)

An Acme responder opens a **resolution session** with symptoms `["VPN auth failure"]` and external case ids; trace events record runtime match results; when they **start execution** on the certificate rotation playbook, safety caps allow only **low side effect** steps unless a **domain admin** approves higher risk; audit rows show who changed playbook state during the incident bridge.

## Further reading

- [02-api-and-request-lifecycle.md](./02-api-and-request-lifecycle.md) — middleware audit and auth  
- [07-episodes-patterns-playbooks.md](./07-episodes-patterns-playbooks.md) — approval and publication  
- [`docs/API.md`](../docs/API.md) — sessions and execution routes  
