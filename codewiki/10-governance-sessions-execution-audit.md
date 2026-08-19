# Governance: sessions, execution, and audit

## Summary

You will see how a **resolution session** captures an incident's context and its **decision trace**, how **playbook execution** is gated step by step — lifecycle, expiry, safety class, approval policy, action policy, trust, and artifact binding — how the **step ledger** records what an executor actually did, how **post-action verification** decides whether a fix really held, and how **audit logs** and **operational events** provide two complementary trails.

## Business picture

When teams resolve outages, the platform records **what was known** (symptoms, related tickets), **what was recommended** (matched playbooks and confidence scores), and **what was decided** (approvals, modifications, denials, escalations). Months later, someone can still answer "who did what, and why" without chasing chat logs.

When automation runs a playbook, safety controls stop destructive actions from happening unless the right people approved them — and the approval is tied to the *exact* version of the step that was shown to the approver, so nothing can be edited underneath it. After the run, the platform goes back and checks reality: did new incidents appear on the affected machine, did the alerts stop, did anybody actually say it was fixed? A run that touched a machine which reports nothing at all is recorded as **unverifiable**, not as success — silence is not evidence.

Two honest caveats, because they change how you should read this page:

- **There is no executor on this branch.** `execution_service` is a governed *ledger* driven by external callers over HTTP. Every control described here is real and enforced, but the thing that would actually run a command does not exist yet, so this is a set of prerequisites rather than live exposure ([KNOWN_GAPS.md](./KNOWN_GAPS.md)).
- **Role grants are tenant-wide.** `RoleBinding` stores a scope, but nothing enforces it — see [14-control-plane-tenants-roles-policies.md](./14-control-plane-tenants-roles-policies.md).

## Technical walkthrough

### 1. Resolution sessions

- `create_resolution_session` inserts a `ResolutionSession` with symptoms, entities, external case ids, and an optional domain, at status **`"open"`** (`backend/src/contextedge/services/session_service.py:38-58`). It then records a case transition `None → open` (`session_service.py:62-71`), writes a `session.created` operational event carrying the short-term memory class (`session_service.py:72-88`), and fires a fire-and-forget `review_queue.prefetch_review_context` Celery task so the reviewer console's first render hits Redis instead of Postgres. The enqueue is wrapped in try/except: a degraded broker must never block session creation (`session_service.py:18-35`).
- `append_trace_event` appends a `DecisionTraceEvent` row (inputs, outputs, reasoning, confidence) **and** an operational event named `decision_trace.<event_type>` tagged with the reasoning memory class (`session_service.py:139-181`). A trace event on a session that does not exist returns `None` rather than raising.
- `get_resolution_session` eager-loads trace events with `selectinload`, and first-class `Decision` rows too when `include_decisions=True` (`session_service.py:93-111`).
- `close_resolution_session` is where a session states what it *meant*: an optional `outcome` dict (`outcome_status`, `resolution_summary`, `confirmed_root_cause`, `successful_action`, `failed_actions`, `user_confirmed`, `fix_results`) writes a `CaseOutcome`; a close without one records only the transition, because an unstated outcome is unknown, not "resolved" (`session_service.py:184-238`). Re-closing an already-closed session is a deliberate no-op for history (`session_service.py:208-215`), and closing invalidates the cached review bundle (`session_service.py:251-252`).
- HTTP: `backend/src/contextedge/api/v1/sessions.py` (list 26, create 45, get 64, append event 76, history 102, close 139).

### 2. Starting an execution — the gate order

`start_execution` (`backend/src/contextedge/services/execution_service.py:638-1008`) runs these checks in this order. Order matters: each one can refuse, and the ones that write audit rows do so **on the deny path too**.

1. **Playbook exists and is in-tenant** → otherwise `ExecutionPolicyError` (`execution_service.py:650-652`).
2. **Lifecycle state is `approved`** (`execution_service.py:653-657`).
3. **Not expired** — an explicit `expiry_at` in the past refuses, even while the row still reads `approved`. Drift detection flips expired playbooks on its own schedule, but between beats this is the guard that holds (`execution_service.py:658-667`).
4. **Resolve a published version** — an explicit `playbook_version_id` must belong to the playbook and must have `published_at`; otherwise the latest published version wins, and "no published version" refuses (`execution_service.py:669-687`).
5. **Approval policy: automation-mode cap.** `load_approval_policy` fails closed on a dangling, inactive, or wrong-type reference — a broken governance pointer must never silently disable governance (`backend/src/contextedge/services/approval_policy_service.py:22-24, 63-104`). `check_automation_mode` refuses a mode more autonomous than the policy's `max_automation_mode` (`approval_policy_service.py:106-117`). **Both outcomes are recorded** as a `policy_checks` row — the denial is the evaluation an implementation that records only successes loses (`execution_service.py:696-712`).
6. **Effective safety cap** = the stricter of the caller's requested cap and their role-derived cap. `_caller_max_safety_class` (`execution_service.py:615-635`): `suggest_only` → `read_only` for everyone; admin roles get `destructive` under `full_auto` and `high_side_effect` otherwise; `knowledge_manager` gets `low_side_effect`; everyone else `read_only`. Shadow mode lifts the cap (every call is a dry run) but still keeps `destructive` behind an admin role.
7. **Create the run row** at status `running` with the effective cap stamped on it (`execution_service.py:721-733`).
8. **Trust veto.** A `TrustProfile` at `autonomy_level == "suspended"` for this actor blocks the run and records a `trust_scope` policy check. `advisory` and `supervised` are recorded as context, never enforced — treating "unproven" as "forbidden" would stop any new action from ever earning a record (`execution_service.py:289-352`). **Trust vetoes, it never grants.**
9. **Shadow dry-run check.** In shadow mode, a step bound to a skill whose contract says it cannot be dry-run is refused: running it "in shadow" would either do the real thing or silently do nothing (`execution_service.py:742-748`).
10. **A version with no steps refuses** — a run that creates no step rows, requests no approvals, and reports success is an execution record attesting to work nobody did (`execution_service.py:750-760`).
11. **Per step**: read `safety_class` / `requires_approval` / declared action identity; force approval when the step outranks the effective cap, when the policy's `require_approval_min_safety_class` says so, or when the **action policy** returns `approval_required`. The action-policy engine can force approval or refuse, and `allowed_auto` **grants nothing** (`execution_service.py:764-819`; `backend/src/contextedge/services/action_policy_service.py`).
12. **Idempotency keys** are assigned after the flush, and only to steps whose replay is worth suppressing — `read_only` steps are excluded, because a key that blocked a second status check would be a bug wearing a safety control's clothes (`execution_service.py:823-830`; `backend/src/contextedge/services/idempotency_service.py:40-60`).
13. **Approval requests** are created for every gated step, each bound to the artifact: `artifact_version`, an RFC 8785 canonical `artifact_hash` of that exact step, a `policy_snapshot`, and an `expires_at` (`execution_service.py:832-871`; `backend/src/contextedge/services/artifact_binding_service.py:47-121`, validity `APPROVAL_VALIDITY_HOURS = 4`).
14. **Shadow runs never block.** The approval rows are still created so "what would this run have asked for?" stays queryable, then immediately stamped approved with the comment `"shadow mode — auto-approved (no human intervention)"`, and the run plus every gated step is forced back to `running` (`execution_service.py:876-898`).
15. **Graph + trail**: an `executes` edge from run to playbook (`execution_service.py:902-912`); an `execution.started` operational event (`execution_service.py:914-932`); when a session is present, a trace event plus `executed_playbook` (session → playbook) and `has_execution` (session → run, carrying the *session's* domain per the canonical domain rule) edges (`execution_service.py:934-976`); and a first-class `Decision` of type `execute_playbook` carrying the strictest action-policy verdict any step drew — NULL when no rule matched, because "no rule existed" must not read as "a rule permitted it" (`execution_service.py:978-1006`).

**Vocabulary, exactly as the code defines it** (`backend/src/contextedge/models/execution.py:10-17`):

- `SAFETY_CLASSES = ("read_only", "low_side_effect", "high_side_effect", "destructive")` — there is no "medium".
- `STEP_STATUSES = ("pending", "running", "awaiting_approval", "completed", "skipped", "failed")`
- `APPROVAL_STATUSES = ("pending", "approved", "denied", "modified", "expired")`
- `OUTCOMES = ("success", "partial", "failure", "aborted")`
- `AUTOMATION_MODES = ("suggest_only", "shadow", "human_confirmed", "supervised", "full_auto")` (`backend/src/contextedge/models/playbook.py:30-36`)

### 3. Deciding an approval

`decide_approval` (`execution_service.py:1314-1461`):

- The row is read `SELECT ... FOR UPDATE` so two concurrent decide/modify calls serialise at the database; the loser sees a non-pending status and raises (`execution_service.py:1324-1336`).
- `check_decider` enforces the policy's `approver_roles` and `forbid_self_approval` at **decide** time, not just at save time; both the pass and the fail are written to `policy_checks` (`execution_service.py:1341-1373`; `approval_policy_service.py:127-149`).
- **Denied** aborts the run and marks the step failed — with an explicit `step.tenant_id == tenant_id` guard before mutating (`execution_service.py:1382-1399`). **Approved** returns the run and step to `running` (`execution_service.py:1400-1411`).
- Trail: an `approval.approved` / `approval.denied` operational event, an `approved_by` / `denied_by` graph edge to the deciding user, and a first-class `Decision` (`execution_service.py:1415-1459`).
- `modify_approval` is the third verb of the reviewer console's **Approve / Modify / Reject** flow, carrying a non-empty `modification_diff` and a `modification_reason_code` from a fixed vocabulary — both validated before anything is written (`execution_service.py:1464, 1489-1494, 1538-1539`).
- Separation of duties is enforced only on the **initiator ↔ approver** axis. There is no recommender ↔ approver check — `recommended_by` and `sod_check_status` exist as columns and nothing writes them (`backend/src/contextedge/models/execution.py:214-216`; the only reader is the agent hydrator at `backend/src/contextedge/graph/agent/hydrators.py:387`). See [KNOWN_GAPS.md](./KNOWN_GAPS.md), F7 residual.
- Pending approvals nobody decided expire after `APPROVAL_EXPIRY_HOURS = 72`, swept 200 at a time by the verification beat. **Expiry never approves** — the step stays blocked and the requester re-raises with current context (`backend/src/contextedge/services/approval_expiry_service.py:27-31`; `backend/src/contextedge/workers/verification_tasks.py:85-102`).

### 4. The step ledger (what an executor drives)

Two service functions carry the last-moment controls, exposed on HTTP so an external executor can drive them:

- `POST /api/v1/execution/runs/{run_id}/steps/{step_run_id}/invocations` → `record_tool_invocation` (`backend/src/contextedge/api/v1/execution.py:135-176`). Order of refusals inside the service (`execution_service.py:1136-1230`): a step already recognised as a **duplicate** must not invoke anything; an invocation may not declare a **higher safety class than its own step** (that would record a destructive call under a step approved as read-only, with every upstream control still reading as satisfied); then `assert_approved_artifact_unchanged` re-checks F7's binding — the artifact about to run must still be the one that was approved, and the approval must not have gone stale. Every accepted call also writes one `ExecutionAttempt` row, so "did this run twice?" has an answer.
- `POST /api/v1/execution/runs/{run_id}/steps/{step_run_id}/complete` → `record_step_completion` (`api/v1/execution.py:179-213`). A step still awaiting its approval cannot be reported complete, or `complete_execution`'s open-steps check would pass with an undecided approval underneath it (`execution_service.py:1096-1103`).

Both routes require the run's **initiator or a `domain_admin`** (`api/v1/execution.py:36-47`) and require the step to belong to the run in the URL (`api/v1/execution.py:122-133`). Service refusals surface as **409, not 500** — a duplicate replay and a stale binding are well-formed requests the state declines (`api/v1/execution.py:171-172, 206-207`). The request body deliberately carries **no attempt number and no idempotency key**: both are derived from what is already recorded, and a caller that can hand in the key the duplicate check tests against can defeat the control by asserting the answer.

`complete_execution` refuses `success`/`partial`/`failure` while any step is still `pending`, `running`, or `awaiting_approval` (`execution_service.py:1641-1661`), then writes the `execution.<outcome>` event, a session trace event, an `execution_outcome` edge to the playbook, and a `DecisionOutcome` on the run's own `execute_playbook` decision — matched by `execution_run_id` inside `context_snapshot` so a session with several runs cannot attach an outcome to the wrong decision (`execution_service.py:1670-1729`). `abort_execution` is `complete_execution` with outcome `aborted` (`execution_service.py:1734-1747`).

### 5. Post-action verification (does the fix hold?)

The beat task `evaluation.verify_executions` runs every 15 minutes on the evaluation queue (`backend/src/contextedge/workers/verification_tasks.py:108-121`). Its queue is the partial index from migration `0036`: completed runs whose outcome is `success` or `partial` and whose `verification_status IS NULL`, oldest first, `SWEEP_LIMIT_PER_TENANT = 50` (`verification_tasks.py:26, 51-70`).

`verify_execution_run` (`backend/src/contextedge/services/execution_verification_service.py:587-782`):

1. **Not due yet?** The per-playbook `recheck_after_sec` defaults to 1800 s with a 300 s floor; a run before its recheck time returns `not_due` and stays queued — the only outcome that persists nothing (`execution_verification_service.py:56-70, 607-614`).
2. **Resolve the CIs** the session named (`execution_verification_service.py:78-101`).
3. **Evaluate each criterion separately** (`execution_verification_service.py:311-402`). Three criterion types exist today: `incident_absence`, `alert_absence`, and `user_confirmation`. Absence **passes only when the CI has actually reported something in the last `OBSERVABILITY_LOOKBACK_DAYS = 30` days**; otherwise the criterion is `not_observable` with the detail "silence here is not evidence." Alert-only verdicts are re-confirmed against the alert batches' own event times so a closing storm after a good fix cannot produce a false failure (`execution_verification_service.py:343-349`). `user_confirmation` reads the existing `message_function` classification for a `resolution_confirmation` message.
4. **Aggregate and persist** — a `VerificationAssessment` plus one `VerificationObservation` per criterion, and the legacy `verification_status` (`verified` / `failed` / `unverifiable`) on the run itself (`execution_verification_service.py:405-443, 653-663`).
5. **Act on the verdict, all fail-soft**: `rollback_recommended` derives a `RollbackPlan` (reverse step order, `infeasible` when there is no way back); `escalation_required` raises an `Escalation` carrying **refs, never copies** — a copy would be a second version of the truth that ages away from the first (`execution_verification_service.py:446-516`). Nothing executes here: running an undo is an `ExecutionRun` with `rolls_back_run_id` set, so it inherits the same approval, attempt, and verification machinery.
6. **Feed the learning loops**, each wrapped so it can never break the verification that produced it: fix-cohort counters (`execution_verification_service.py:665-697`), trust profiles per (action type × CI class × environment × criticality) (`execution_verification_service.py:519-584, 710-721`), and knowledge support for the playbook version's cited articles (`execution_verification_service.py:727-750`).
7. **Emit** `execution.verification_completed`, and on a verified run whose policy sets `auto_close_on_success`, `execution.auto_close_recommended` — it **recommends, never closes** a human's session (`execution_verification_service.py:752-774`).

Trust scoring uses a Wilson lower bound rather than a raw success rate, and suspends on a recent-failure streak regardless of history: `WILSON_Z = 1.96`, `AUTONOMOUS_MIN_LOWER_BOUND = 0.90`, `SUPERVISED_MIN_LOWER_BOUND = 0.50`, `SUSPEND_AFTER_CONSECUTIVE_FAILURES = 3`; only `success` counts as a success, while `failed` / `rollback_required` / `partial_success` count against (`backend/src/contextedge/services/trust_service.py:42-51, 150-151`).

### 6. Two audit channels, one event stream

**Channel 1 — HTTP middleware audit.** `RequestAuditMiddleware` fires *after* the response for every `POST/PATCH/PUT/DELETE` under `/api/v1` except `/auth/login` (`backend/src/contextedge/middleware/request_audit.py:29-124`). It always writes one structlog `http.mutating_request` line, and additionally inserts an `audit_logs` row when a tenant was resolved, with `action = "http.<method>.<path-slug>"` and an outcome derived from the status: `<400` success, 401/403 **denied**, otherwise **failed**. The insert runs on a lazily created **sync** engine off-thread and swallows its own failures as `audit_db_error` — auditing must never break a request (`request_audit.py:89-122`).

> **Scope note, baked into the code:** an unauthenticated 401 probe never resolves a tenant, so it exists **only** in structlog. Alert on `http.mutating_request` with status 401 for those (`request_audit.py:59-64`).

**Channel 2 — explicit audit.** `log_audit_event` writes an `AuditLog` row and merges `request_id` / `correlation_id` / `causation_id` from the request context into `details`, defaulting actor and email from the same context (`backend/src/contextedge/middleware/audit.py:10-44`). Control-plane mutations and sync control actions call it directly. Read surface: `GET /api/v1/audit-logs`, gated to `tenant_admin` (`backend/src/contextedge/api/v1/audit.py:14-26`).

**The event stream.** `append_operational_event` writes an `OperationalEvent` with entity type/id, optional session, correlation/causation, actor, and a JSONB payload — and it inherits correlation, causation, and actor from the request context automatically, which is what makes one browser click traceable into a worker's events (`backend/src/contextedge/services/event_log_service.py:32-61`). `list_operational_events` filters by tenant, entity, session, or correlation id, newest first by `recorded_at`, default limit 100 (`event_log_service.py:64-85`).

Governance event types this layer writes, verified at their call sites: `session.created` / `session.closed` (`session_service.py:79, 245`), `decision_trace.<type>` (`session_service.py:171`), `execution.started` (`execution_service.py:921`), `execution.<outcome>` (`execution_service.py:1675`), `execution_step.<status>` (`execution_service.py:1125`), `tool.<status>` and `tool.shadow_executed` (`execution_service.py:1220`), `approval.requested` (1303), `approval.approved` / `approval.denied` (1421), `approval.modified` (1563), `approval.binding_violated` (580), `execution.step_deduplicated` (485), `execution.approval_expired` (`approval_expiry_service.py:65`), `execution.verification_completed` and `execution.auto_close_recommended` (`execution_verification_service.py:757, 769`).

**When to use which:** `audit_logs` answers compliance questions about *who changed what over HTTP*; `operational_events` answers timeline questions about *what the system did to this entity*, including work that never had an HTTP request behind it. Both are append-only in practice; neither is a substitute for the other.

### 7. Runtime explain cache

Runtime match responses cache their explain payload in Redis under `runtime:match:<match_id>` (`backend/src/contextedge/api/v1/runtime.py:233, 252`). A playbook lifecycle transition can pass `redis` to `playbook_service.transition_playbook`, which then runs `scan_iter` over `runtime:match:*` and deletes the keys whose cached payload names this tenant, so a cached `/runtime/explain` answer cannot outlive the transition it describes (`backend/src/contextedge/services/playbook_service.py:325-326, 331-357`). The key is opaque, which is why it takes a scan rather than a targeted delete; Redis being unavailable only logs — the transition still commits and stale entries simply age out on their TTL.

## Example: Acme VPN data at this stage

**Input — a responder opens a resolution session**

```json
{
  "tenant_id": "acme-corp",
  "symptoms": ["VPN authentication failure", "users cannot connect to corporate network"],
  "entities": ["vpn-gw-east-01"],
  "external_case_ids": ["INC0010427"],
  "domain_id": "vpn-connectivity"
}
```

**Output — the session, with its decision trace**

```json
{
  "session_id": "sess-abc123",
  "status": "open",
  "created_at": "2026-08-19T10:30:00Z",
  "trace_events": [
    {
      "event_type": "retrieve",
      "inputs": { "symptoms": ["VPN authentication failure"] },
      "outputs": { "top_match": "pb-r1s2t3", "confidence": 0.92 },
      "reasoning": "Hybrid ranking matched the VPN certificate-rotation playbook on keyword, semantic and graph signals",
      "timestamp": "2026-08-19T10:30:05Z"
    }
  ]
}
```

**Input — start execution on the approved playbook**

```json
{
  "playbook_id": "pb-r1s2t3",
  "session_id": "sess-abc123",
  "max_safety_class": "low_side_effect"
}
```

**Output — the run, with the caps and gates applied**

```json
{
  "execution_run_id": "exec-def456",
  "playbook_version": "1.2.0",
  "automation_mode": "supervised",
  "max_safety_class": "low_side_effect",
  "status": "awaiting_approval",
  "steps": [
    { "step_index": 0, "step_title": "Confirm AUTH_CERT_EXPIRED on vpn-gw-east-01", "safety_class": "read_only", "status": "pending", "requires_approval": false },
    { "step_index": 1, "step_title": "Check certificate expiry date", "safety_class": "read_only", "status": "pending", "requires_approval": false },
    { "step_index": 2, "step_title": "Renew gateway certificate via internal CA", "safety_class": "high_side_effect", "status": "awaiting_approval", "requires_approval": true }
  ]
}
```

Step 2 is `high_side_effect`, which outranks the caller's `low_side_effect` cap, so `start_execution` forced `requires_approval` and created an approval request bound to that exact step — and `request_approval` flipped both that step and the run to `awaiting_approval` (`execution_service.py:1270-1275`). The two read-only steps are left at `pending`: nothing gates them, and nothing runs them either, because the executor that would drive the ledger does not exist on this branch.

**Output — the approval request, bound to the artifact**

```json
{
  "approval_request_id": "apr-step2",
  "execution_run_id": "exec-def456",
  "step_run_id": "sr-step2",
  "requested_action": "execute_step:2",
  "safety_class": "high_side_effect",
  "artifact_version": "1.2.0",
  "artifact_hash": "sha256:9f2c...",
  "approver_role": "domain_admin",
  "policy_snapshot": { "forbid_self_approval": true, "approver_roles": ["domain_admin"] },
  "expires_at": "2026-08-19T14:30:00Z",
  "status": "pending"
}
```

**Output — governed decision edges written during the run**

```json
[
  { "edge_type": "executes",           "source": {"type": "execution_run", "id": "exec-def456"}, "target": {"type": "playbook", "id": "pb-r1s2t3"}, "metadata": {"automation_mode": "supervised"} },
  { "edge_type": "executed_playbook",  "source": {"type": "session", "id": "sess-abc123"},       "target": {"type": "playbook", "id": "pb-r1s2t3"} },
  { "edge_type": "has_execution",      "source": {"type": "session", "id": "sess-abc123"},       "target": {"type": "execution_run", "id": "exec-def456"} },
  { "edge_type": "approved_by",        "source": {"type": "approval_request", "id": "apr-step2"},"target": {"type": "user", "id": "vpn-lead@acme.com"}, "metadata": {"comment": "Safe to renew during the maintenance window", "safety_class": "high_side_effect"} },
  { "edge_type": "execution_outcome",  "source": {"type": "execution_run", "id": "exec-def456"}, "target": {"type": "playbook", "id": "pb-r1s2t3"}, "metadata": {"outcome": "success", "outcome_summary": "VPN gateway certificate renewed"} }
]
```

**Output — post-action verification, 30 minutes later**

```json
{
  "verification_status": "verified",
  "verified_at": "2026-08-19T11:20:00Z",
  "verification_details": {
    "assessment": "success",
    "summary": "no new incidents or alerts on vpn-gw-east-01, and one user confirmation",
    "checked_cis": ["vpn-gw-east-01"],
    "criteria": [
      { "type": "incident_absence",  "status": "pass", "observed": {"count": 0, "ci_observable": true} },
      { "type": "alert_absence",     "status": "pass", "observed": {"count": 0, "ci_observable": true} },
      { "type": "user_confirmation", "status": "pass", "observed": {"confirmations": 1} }
    ],
    "recheck_after_sec": 1800,
    "assessment_id": "va-778899"
  }
}
```

Had `vpn-gw-east-01` never produced an incident or alert in the previous 30 days, the two absence criteria would read `not_observable` and the run would be recorded **`unverifiable`** instead — which is the correction that F9 shipped, not a regression.

## Design decisions

- **Sessions and execution runs are separate objects** — *Why:* a session is investigative narrative, an execution is governed action with a safety cap; collapsing them would force every investigation to carry execution's controls. *Tradeoff:* operators must link the two mentally when one incident has both; the `has_execution` and `executed_playbook` edges exist so the graph can do it for them.

- **A close without an outcome records the transition only** — *Why:* an unstated outcome is unknown, and defaulting it to "resolved" would feed silence into the MTTR and first-time-right metrics as success (`session_service.py:192-197`). *Tradeoff:* outcome coverage depends on responders filling in the close payload, so a low coverage rate is a real (and visible) data-quality problem rather than a hidden optimistic bias.

- **Denials are recorded, not just approvals** — *Why:* the refusal is the evaluation an implementation that logs only the success path loses, and it is precisely what a compliance question asks about (`execution_service.py:701-709`). *Tradeoff:* `policy_checks` grows on every gate evaluation; the writes are fail-soft, so a missing check row means the audit is incomplete, never that the action was wrong.

- **Approval is bound to an artifact hash, re-checked at the last moment** — *Why:* `PlaybookVersion.steps` was mutable JSONB, so "which exact thing did the human approve?" could not be expressed; the hash makes editing a step under a standing approval detectable (`artifact_binding_service.py:60-121`; re-checked at `execution_service.py:1175-1178`). *Tradeoff:* the hash is a self-consistency check, **not a signature** — it proves the payload did not change, not who produced it; approvals predating the feature carry no hash and are allowed through with a log.

- **Trust vetoes, never grants** — *Why:* a measured track record should be able to stop an action, but letting an excellent record *authorise* one would turn statistics into an automatic escalation of privilege (`execution_service.py:735-740`). *Tradeoff:* `advisory` and `supervised` levels are recorded and unenforced, so the trust table currently does less than its schema suggests — deliberately, because treating "unproven" as "forbidden" is how trust systems get switched off.

- **Absence passes only from a CI that actually reports** — *Why:* the old rule returned `verified` whenever nothing bad happened, so a machine that had stopped emitting telemetry read as a success and fed the cohort learning loop (`execution_verification_service.py:358-367`). *Tradeoff:* runs that used to read `verified` now read `unverifiable`, and downstream counters drop accordingly — that is the correction, not a regression.

- **Ledger refusals are 409, not 500** — *Why:* a duplicate replay or a stale binding is a well-formed request that the current state declines, and an external executor needs to tell "you may not" apart from "we broke" (`api/v1/execution.py:149-153`). *Tradeoff:* callers must handle 409 as a normal terminal outcome rather than as a retryable error.

- **Two audit sinks, one event stream** — *Why:* compliance export and domain timeline are different query shapes; forcing one table to serve both makes each query worse. *Tradeoff:* implementers must pick the right sink per change, and unauthenticated denials land in structlog only.

- **Governed decision edges live in the same `GraphEdge` table as everything else** — *Why:* "which playbooks has this user approved?" becomes a traversal instead of a bespoke join (`execution_service.py:1429-1438`). *Tradeoff:* edge volume grows with execution activity, and the projection allowlist has to keep pace with new edge types.

## Code map

| Concern | Module path | Key symbols | When it runs |
| --- | --- | --- | --- |
| Sessions | `backend/src/contextedge/services/session_service.py` | `create_resolution_session` (38), `append_trace_event` (139), `close_resolution_session` (184) | API / runtime |
| Session models | `backend/src/contextedge/models/session.py` | `ResolutionSession` (11), `DecisionTraceEvent` (101), `CaseLink` (148) | ORM |
| Sessions API | `backend/src/contextedge/api/v1/sessions.py` | create (45), append event (76), history (102), close (139) | HTTP |
| Execution gates | `backend/src/contextedge/services/execution_service.py` | `start_execution` (638), `_caller_max_safety_class` (615), `_enforce_trust_suspension` (289), `_apply_action_policy` (206), `_assign_idempotency_keys` (403) | Run start |
| Approval decisions | `backend/src/contextedge/services/execution_service.py` | `request_approval` (1233), `decide_approval` (1314), `modify_approval` (1464) | Reviewer console |
| Step ledger | `backend/src/contextedge/services/execution_service.py` | `record_tool_invocation` (1136), `record_step_completion` (1085), `assert_approved_artifact_unchanged` (518), `_record_attempt` (355) | External executor |
| Run lifecycle | `backend/src/contextedge/services/execution_service.py` | `complete_execution` (1625), `abort_execution` (1734), `ExecutionPolicyError` (47) | API |
| Execution vocabulary | `backend/src/contextedge/models/execution.py` | `SAFETY_CLASSES` (10), `STEP_STATUSES` (12), `APPROVAL_STATUSES` (16), `OUTCOMES` (17) | ORM |
| Execution API | `backend/src/contextedge/api/v1/execution.py` | `create_execution_run` (66), `record_invocation` (140), `complete_step` (183), `decide_on_approval` (263), `_require_run_control` (36) | HTTP |
| Approval policy | `backend/src/contextedge/services/approval_policy_service.py` | `load_approval_policy` (63), `check_automation_mode` (106), `step_requires_policy_approval` (119), `check_decider` (127) | Start + decide |
| Policy-check ledger | `backend/src/contextedge/services/policy_check_service.py` | `record_policy_check` (34) | Every gate evaluation |
| Artifact binding | `backend/src/contextedge/services/artifact_binding_service.py` | `canonical_hash` (60), `hash_step_artifact` (100), `verify_binding` (123), `APPROVAL_VALIDITY_HOURS` (47) | Approval + invocation |
| Idempotency | `backend/src/contextedge/services/idempotency_service.py` | `needs_idempotency_key` (47), `derive_idempotency_key` (62), `find_duplicate` (82) | Run start + invocation |
| Approval expiry | `backend/src/contextedge/services/approval_expiry_service.py` | `expire_stale_approvals` (31), `APPROVAL_EXPIRY_HOURS = 72` (27) | Verification beat |
| Verification | `backend/src/contextedge/services/execution_verification_service.py` | `verify_execution_run` (587), `_evaluate_criteria` (311), `_act_on_verdict` (446), `_record_trust_outcomes` (519) | Beat, 15 min |
| Verification sweep task | `backend/src/contextedge/workers/verification_tasks.py` | `verify_executions` (112), `SWEEP_LIMIT_PER_TENANT` (26) | evaluation queue |
| Trust profiles | `backend/src/contextedge/services/trust_service.py` | `wilson_lower_bound` (53), `evaluate_autonomy` (70), `scope_key` (107), `record_outcome` (154) | After verification |
| HTTP audit rows | `backend/src/contextedge/middleware/request_audit.py` | `RequestAuditMiddleware.dispatch` (29) | Every mutating `/api/v1` call |
| Explicit audit | `backend/src/contextedge/middleware/audit.py` | `log_audit_event` (10) | Control-plane mutations |
| Operational events | `backend/src/contextedge/services/event_log_service.py` | `append_operational_event` (32), `list_operational_events` (64) | Throughout |
| Event model | `backend/src/contextedge/models/events.py` | `OperationalEvent` (13) | ORM |
| Audit API | `backend/src/contextedge/api/v1/audit.py` | `list_audit_logs` (15) | HTTP, `tenant_admin` |

## Acme VPN incident (this layer)

An Acme responder opens a **resolution session** with the symptom "VPN authentication failure" and the external case id `INC0010427`; the runtime match writes a `retrieve` trace event naming the certificate-rotation playbook at 0.92 confidence. Starting execution under `supervised` mode with a `low_side_effect` request leaves the two read-only diagnostic steps ungated, and forces an approval on step 2 — renewing the gateway certificate is `high_side_effect`, above the caller's cap. The approval request is stamped with the hash of that exact step in version 1.2.0 and expires in four hours. The VPN domain admin approves it; the `approved_by` edge records who, when, and under which safety class, and a `policy_checks` row records that `forbid_self_approval` was evaluated and passed. Thirty minutes after completion the verification sweep re-checks `vpn-gw-east-01`: no new incidents, no new alert batches, and a Teams message classified as `resolution_confirmation` — verdict **verified**, folded into the trust profile for that action on that CI class. Meanwhile `audit_logs` shows every mutating call anyone made during the incident bridge, and `operational_events` shows the whole timeline joined by one correlation id.

## Further reading

- [02-api-and-request-lifecycle.md](./02-api-and-request-lifecycle.md) — auth, service tokens, and the middleware that mints correlation ids
- [07-episodes-patterns-playbooks.md](./07-episodes-patterns-playbooks.md) — playbook approval, publication, and expiry
- [08-workers-celery-queues.md](./08-workers-celery-queues.md) — the beat entry that drives verification and approval expiry
- [11-retention-and-operational-events.md](./11-retention-and-operational-events.md) — how long these records live
- [14-control-plane-tenants-roles-policies.md](./14-control-plane-tenants-roles-policies.md) — where approval policies are authored, and the role-scope caveat
- [16-decision-traces.md](./16-decision-traces.md) — first-class decision traces and analytics
- [`docs/API.md`](../docs/API.md) — session and execution route details
- [KNOWN_GAPS.md](./KNOWN_GAPS.md) — the executor gap, the SoD residual, and what the skill registry still lacks
