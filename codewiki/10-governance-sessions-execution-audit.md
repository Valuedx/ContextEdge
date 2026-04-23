# Governance: sessions, execution, and audit

## Summary

You will see how **resolution sessions** capture an incident's context and **decision trace** events, how **playbook execution** enforces **safety classes** and publication rules, and how **audit logs** and **operational events** provide complementary trails for compliance and debugging.

## Business picture

When teams resolve outages, the platform records **what was known** (symptoms, related tickets), **what was recommended** (matched playbooks and confidence scores), and **what was decided** (approvals, overrides, escalations) — creating an auditable trail that can be reviewed days or months later. When automation runs a playbook, safety controls prevent destructive actions unless the right people have approved them. Every sensitive change is attributable to a specific person and searchable after the fact, so compliance teams can answer "who did what and why" without chasing down chat logs.

## Technical walkthrough

### Resolution sessions

- `session_service.create_resolution_session` inserts `ResolutionSession` with symptoms, entities, external case ids, optional domain, and `append_operational_event` records `session.created` with memory class metadata.
- `append_trace_event` appends `DecisionTraceEvent` rows (inputs, outputs, reasoning, confidence) for AI or runtime steps tied to the session. These compact traces remain for backward compatibility and lightweight audit trails.
- `get_resolution_session` loads trace events via `selectinload`, and optionally eager-loads first-class `Decision` objects via `include_decisions=True`.
- Sessions also expose a `decisions` relationship to first-class `Decision` rows, providing the richer, graph-connected reasoning records alongside the flat trace events.
- HTTP: `api/v1/sessions.py`.

### Execution

- `execution_service.start_execution` loads the playbook, enforces `lifecycle_state == "approved"` **and** `expiry_at >= now()` (review F-12 — playbooks past their expiry no longer execute even when still labelled approved), resolves a **published** `PlaybookVersion` (explicit id or latest published), computes caller **max safety class** from roles + playbook `automation_mode` vs `requested_max_safety_class`, and creates `ExecutionRun` plus related structures; integrates with `append_trace_event` and `append_operational_event` for traceability. It also creates a `session --(executed_playbook)--> playbook` graph edge when a session is present. **Shadow-mode behaviour**: when `automation_mode == "shadow"`, approval requests are still *created* so the audit trail records what a real run would have asked for, but they are immediately auto-approved with the comment `"shadow mode — auto-approved (no human intervention)"` and the run + every approval-gated step_run are flipped back to `running` (review F-13). A shadow run therefore never blocks waiting for a human.
- `decide_approval` creates `approval_request --(approved_by / denied_by)--> user` graph edges when managers approve or deny steps, recording the comment and safety class. It also creates a first-class `Decision` with `decision_type="approve"` or `"deny"`. The approval request is loaded via `SELECT ... WITH FOR UPDATE` (review F-15) so two concurrent decide / modify calls on the same approval serialise at the DB; the second sees `req.status != "pending"` and raises `ExecutionPolicyError`. The deny branch guards `step_run.tenant_id == tenant_id` before mutating the step (review F-11), closing a cross-tenant code-level invariant gap that the `modify_approval` branch already covered. `playbook_service.transition_playbook` accepts an optional `redis` kwarg; when passed, a lifecycle transition runs `scan_iter + delete` over `runtime:match:*` for the tenant so cached `/runtime/explain` responses can't survive past the transition (review F-09).
- `complete_execution` creates an `execution_run --(execution_outcome)--> playbook` graph edge with the outcome and summary, and records a `DecisionOutcome` on the execution's first-class `Decision`.
- These **governed decision edges** form the high-fidelity tier of the decision graph — every approval, denial, and execution outcome is directly attributable to a person and fully auditable. Since the introduction of first-class decision traces, these execution stages also produce `Decision`, `DecisionOption`, and `DecisionOutcome` records that are connected into the context graph with edges like `based_on`, `chose`, `resulted_in`, and `followed_by` (see [16-decision-traces.md](./16-decision-traces.md)).
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

**Output — governed decision graph edges created during execution**

```json
[
  {
    "edge_type": "executed_playbook",
    "source": { "type": "session", "id": "sess-abc123" },
    "target": { "type": "playbook", "id": "pb-r1s2t3" },
    "metadata": { "execution_run_id": "exec-def456", "automation_mode": "supervised" }
  },
  {
    "edge_type": "approved_by",
    "source": { "type": "approval_request", "id": "apr-step3" },
    "target": { "type": "user", "id": "domain-admin@acme.com" },
    "metadata": { "comment": "Safe to renew cert during maintenance window", "safety_class": "medium_side_effect" }
  },
  {
    "edge_type": "execution_outcome",
    "source": { "type": "execution_run", "id": "exec-def456" },
    "target": { "type": "playbook", "id": "pb-r1s2t3" },
    "metadata": { "outcome": "success", "outcome_summary": "VPN gateway certificate renewed" }
  }
]
```

These edges make it possible to traverse the graph from a playbook to see who approved its execution, what sessions triggered it, and what outcomes resulted.

## Design decisions

- **Sessions vs execution runs** — *Why:* a session is investigative narrative; execution is governed action with safety caps. *Tradeoff:* operators must link them mentally when both exist for one incident.

- **Safety class derived from roles + automation mode** — *Why:* prevents "run destructive playbook with read-only integration token." *Tradeoff:* role matrix must stay documented for customers.

- **Published version only for execution** — *Why:* same bar as runtime retrieval—no draft automation. *Tradeoff:* testers need published versions or sandboxes.

- **Operational events + audit logs** — *Why:* different query patterns (domain timeline vs compliance export). *Tradeoff:* implementers choose the right sink per change.

- **Governed decision edges in the context graph** — *Why:* execution approvals, denials, and outcomes become traversable graph relationships, enabling queries like "which playbooks has this user approved?" or "what was the outcome of executions for this playbook?" *Tradeoff:* graph edge volume grows with execution activity; all edges use the same `GraphEdge` model used by correlation and identity linking.

## Code map

| Concern | Module path | Key symbols | When it runs |
| --- | --- | --- | --- |
| Sessions | `backend/src/contextedge/services/session_service.py` | `create_resolution_session`, `append_trace_event`, `get_resolution_session` | API / runtime |
| Session model | `backend/src/contextedge/models/session.py` | `ResolutionSession`, `DecisionTraceEvent`, `CaseLink` | ORM |
| Decision models | `backend/src/contextedge/models/decision.py` | `Decision`, `DecisionOption`, `DecisionOutcome` | ORM |
| Decision service | `backend/src/contextedge/services/decision_trace_service.py` | `create_decision`, `record_outcome`, `get_decision_chain` | Decisions API / execution |
| Decisions API | `backend/src/contextedge/api/v1/decisions.py` | (handlers) | HTTP |
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

An Acme responder opens a **resolution session** with symptoms `["VPN auth failure"]` and external case ids; trace events record runtime match results; when they **start execution** on the certificate rotation playbook, safety caps allow only **low side effect** steps unless a **domain admin** approves higher risk; audit rows show who changed playbook state during the incident bridge. The context graph now records that the session executed the playbook, which domain admin approved the certificate renewal step, and that the execution completed successfully — all traversable as graph edges alongside identity and evidence links.

## Further reading

- [02-api-and-request-lifecycle.md](./02-api-and-request-lifecycle.md) — middleware audit and auth  
- [07-episodes-patterns-playbooks.md](./07-episodes-patterns-playbooks.md) — approval and publication  
- [16-decision-traces.md](./16-decision-traces.md) — first-class decision trace architecture and analytics  
- [`docs/API.md`](../docs/API.md) — sessions and execution routes  
