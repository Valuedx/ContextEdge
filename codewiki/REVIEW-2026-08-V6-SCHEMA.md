# v6 Schema Comparison — Validation and Improvement Plan (2026-08-15)

Validation of an externally-supplied review that scored ContextEdge against
**Context Graph Incident Resolution v6.0** (`Context_Graph_Incident_Resolution_v6.md`,
2,617 lines, 65 KB), plus the improvement plan that follows from what the code
actually does.

**Branch reviewed:** `feature/maf-context-graph-integration` @ `76c4e82`
("feat: domain-scoped writes, governed outcomes, and the last MAF wiring").
**Method:** every falsifiable claim in the pasted review was checked against
models, migrations, services, workers, the MAF projection, and the alembic
chain. 1,527 backend tests collect cleanly at this commit; the suite was not
run (needs a live Postgres).

> **Headline.** The review is directionally right and unusually well-informed —
> roughly 70% of its specific claims are confirmed in code. But it scores a
> **schema** as if it were a **system**. Migration `0029_ae_ops_concept_alignment`
> added 18 execution and decision governance columns that no service writes; the
> review counts several of them as shipped capability. Correcting for that moves the honest
> "production-complete" number from ~80% to **~62%**, and — more usefully —
> changes *which* items are urgent.

---

## 1. Summary

Three things this document establishes:

1. **What the review got right.** Identity resolution, episode clustering, the
   CI-class generalization ladder, negative knowledge, contradiction handling,
   decision traces, playbook versioning and the shadow execution mode are all
   real, all wired, and all roughly as strong as claimed. The review's core
   architectural recommendation — *keep the PostgreSQL implementation, treat v6
   as a semantic contract, map between them* — is correct and should be adopted.

2. **What it got wrong, and why it matters.** The review reads column existence
   as capability. Eighteen columns — six on `execution_step_runs`, eight on
   `approval_requests`, four on `decisions`, including `idempotency_key`,
   `duplicate_check_status`, `sod_check_status`, `recommended_by`,
   `decision_intent` and `policy_result` — are **written by nothing**. The
   action-policy table is never consulted by the executor. Rollback is a text
   field. Escalation has no object. Document "versioning" is a filename
   heuristic that persists nothing.

3. **What nobody flagged.** There is **no executor and no write-capable agent
   tool** on this branch. Every MAF tool is read-or-propose. That single fact
   re-frames the whole risk conversation: the autonomy-safety gaps are
   *prerequisites for a capability that does not exist yet*, not live exposure.
   That is good news, and it buys the sequencing room that Section 7 uses.

---

## 2. Business picture

Someone handed us a specification for how an AI-assisted incident-resolution
system *should* be built, and a report card saying we score 88%. The report card
is mostly fair about the part of the system that reads and reasons — the part
that turns a mess of tickets, chats, emails and alerts into one explainable
story about what broke and what fixed it. That part is genuinely strong.

The report card is too generous about the part that *acts*. It counts a shelf
that was built for a tool as if the tool were on it. ContextEdge today can tell
you what happened, what probably caused it, what fixed the same thing before,
and whether that fix held. It cannot yet safely *press the button itself* — and
the governance machinery that would make pressing the button safe (a signed-off
plan that cannot silently change, a per-tool contract, a duplicate-execution
guard that actually runs, a track record scoped to "this agent, this action,
this kind of machine, this environment") is partly columns without code.

Nothing is on fire, because nothing presses buttons yet. The work in Section 7
is what has to be true *before* the first write-capable tool ships.

---

## 3. Method

| Axis | What was checked |
|---|---|
| Claim confirmation | Every named model, column, enum, service and behaviour in the pasted review, located in code or shown absent |
| Write-path tracing | For each "we have X" claim, whether any service/worker/API *writes* X — not just whether the column exists |
| Blast radius | Which consumers read the field (MAF projection, hydrators, API responses) so unused-column verdicts are precise |
| Spec fidelity | The v6 document itself (§7 temporal, §8 identity, §12 assertion, §14 learning, §16 knowledge, §20 skill, §24 approval, §25 trust, §26 execution, §27 verification, §42 invariants) |
| External grounding | Current practice for canonical hashing, Postgres bitemporality, and reusable ITSM ontologies (Section 9) |

Verdict vocabulary used in Section 4:

- **Confirmed** — claim matches code.
- **Overstated** — the artifact exists but does less than claimed (usually: schema without a writer).
- **Understated** — code does more than the review credits.
- **Wrong** — claim is not supported by code.

---

## 4. Claim-by-claim validation

### 4.1 Confirmed — the review is right about these

| Claim | Evidence |
|---|---|
| `CanonicalIdentity` / `IdentityAlias` / `EvidenceIdentityLink` / `IdentityMergeProposal`, with `resolution_state`, `resolution_confidence`, `resolution_method` | `models/episode.py:48,91,152,318`; states at `:32`; strong-alias tuple `:37-45` |
| Strong aliases uniquely constrained, display names deliberately excluded | Partial unique index `uq_identity_aliases_tenant_strong`, `models/episode.py:104-113` |
| "SFA" vs "Sales Force Automation" / "HP UPD" proposals are persisted, never auto-applied | `models/episode.py:318-332` docstring; `status` default `pending` at `:370` |
| `Episode`, `EpisodeStep`, `EpisodeEvidenceLink`, `CorrelationEdge`, cluster fingerprint, contradictions JSONB, embedding | `models/episode.py:187,213,257,280`; fingerprint `:244`; contradictions `:248` |
| Cross-source clustering is real, bounded and explainable | `services/episode_cluster_service.py:1-58` — connected component over case memberships + correlation edges, 30-day nearest-seed window, `MAX_HOPS=3`, `MAX_CLUSTER_SIZE=50`, per-member `reasons`, stable fingerprint |
| `EntityClass` generalization ladder; OS modelled as a trait, not a class | `models/entity_class.py:1-45` |
| `FixApplicabilityRule` with the seven applicability levels, required/excluded traits, minimum evidence, approval requirement | `models/fix_applicability.py:21-55` |
| First-class `Claim` with validation lifecycle; `ClaimEvidence.support_type`; `DecisionClaim.use_type` constrained to supports/contradicts/risk/precondition | `models/claim.py:57,132,192-197` |
| `Pattern` + `PatternEvidenceLink` + `NegativeKnowledgeItem` + `Contradiction` + `ContradictionScanState` | `models/pattern.py:23,58,85,103,121` |
| One `CaseOutcome` per case at close; reopen creates another; fix result ∈ successful/failed/partial | `models/case_outcome.py:60-61`, CHECK at `:145-148` |
| `GraphEdge.valid_from` / `valid_to` / `confidence`, temporal + current partial indexes, as-of traversal | `models/pattern.py:172-279`; predicate `graph/temporal.py:29-36`; future-dated `as_of` rejected `:21-25` |
| Evidence distinguishes `created_at_source`, `evidence_time`, `ingested_at` | `models/evidence.py:68-99` |
| Operational events distinguish `occurred_at` / `recorded_at` and carry correlation + causation ids | `models/events.py:36-56` |
| `Decision` with context snapshot, options, rejection codes, parent/child chaining, `rationale_summary` (not raw chain-of-thought) | `models/decision.py:43-140`; `DecisionOption` `:143` |
| `shadow` automation mode: goes through the motions, records approvals for audit, short-circuits side effects | `models/playbook.py:30-45`; run path `services/execution_service.py:229-251` |
| Execution verification is real, deterministic, and feeds cohort learning | `services/execution_verification_service.py` — verdicts `verified` / `failed` / `unverifiable`, alert re-delivery guard `:146-184`, cohort write-back `:264-292` |
| `ActionPolicy` verdict vocabulary; precedence explicitly deferred | `models/action_policy.py:37-51`, docstring `:11-14` |
| MAF relationship allowlist including `partially_validated_fix`, with per-type weights and metadata filtering | `graph/agent/profiles.py:89-224`; result→edge mapping `graph/agent/materializer.py:40-44` |
| Multi-tenancy: `Tenant`/`Workspace`/`Domain`/`User`/`RoleBinding`/budgets; evidence sensitivity + access policy + redaction status | `models/tenant.py`; `models/evidence.py:81-86,106` |
| ANN indexing exists via halfvec expression HNSW | `alembic/versions/0032_halfvec_hnsw_indexes.py` |

**Understated by the review — code does more than credited:**

- **Approval policy is enforced, not just referenced.** `services/approval_policy_service.py`
  implements a self-approval ban, approver-role requirement, minimum-safety-class
  approval, and an automation-mode cap — and **fails closed** on a dangling or
  wrong-type policy pointer (`:73-77`). The review's SoD discussion missed this.
- **Identity resolution decisions *are* recorded**, as append-only operational
  events with method, confidence and candidate ids
  (`services/identity_service.py:585-611`, `event_type="identity.resolution_decision"`).
  They are not a first-class table and Layer 2 (exact alias) skips the record —
  but the claim "no resolution decision is persisted" is too strong.
- **Mention capture exists for ticket identifiers.** `PendingIdentifierMention`
  (`models/case_bridge.py:92`) is a genuine `EntityMention` analogue for case
  numbers, with extraction location and order-independent reconciliation.
- **Knowledge is empirically validated against verified executions.**
  `services/knowledge_validation_service.py` computes support levels from
  playbook→knowledge links and *verified* execution outcomes, and explicitly
  refuses to collapse them into one trust score (`:16-33`). Silence is
  `unproven`, never `failing`.

### 4.2 Overstated — the load-bearing corrections

#### O-1 · The `0029` governance columns have no writers *(most important finding)*

The review presents these as shipped capability:

> "already includes: `idempotency_key`, `duplicate_check_status`" · "There is also
> explicit segregation-of-duties data: `recommended_by`, `decided_by`,
> `executed_by`, `sod_check_status`, `sod_violation_reason`. That's
> production-grade thinking."

A repository-wide search for writers of these columns returns **only** the model
definitions, the migration, and one *reader* in the MAF hydrator
(`graph/agent/hydrators.py:360`). No service, worker, API route or task assigns
any of them. The same is true of `ExecutionStepRun.action_name`,
`ExecutionStepRun.execution_mode`, `Decision.decision_intent`,
`Decision.policy_result`, `Decision.risk_level`, `ApprovalRequest.action_name`,
`ApprovalRequest.approver_role` and `ApprovalRequest.approval_channel`.

This is not a discovery — it is stated policy. `codewiki/17-ae-ops-context-graph-alignment.md:173-176`
lists "Service-code population of new fields" as deliberately **out of scope**
for `0029`: *"Schema-first; population can land per-feature."*

Consequences:

- `uq_execution_step_runs_idempotency_key` (migration `0029:534-536`) is a partial
  unique index over a column that is always NULL. The "single most important
  banking-grade safety control" in the alignment doc is currently inert.
- The SoD columns cannot detect anything. Real SoD enforcement exists, but only
  on the initiator↔approver axis via `forbid_self_approval`, and only when a
  tenant configures an approval policy. The recommender↔approver axis (v6 §43.13's
  actual concern — the *agent* that proposed vs the human who approved) is not
  covered.
- `Decision.policy_result` is documented as "the verdict the executor checks"
  (`codewiki/17:126`). The executor does not check it; nothing sets it.

**Update 2026-08-15 (F1 shipped).** The writer audit this finding demanded turned out
to be larger than the 18 columns above. `tests/test_governance_column_writers.py` scans
every `mapped_column` in `models/` for a writer anywhere under `src/contextedge` and
found **79 columns with none** — the `0029` governance spine plus several other
surfaces. Two of the others matter operationally:

- **`FixPattern` has no constructor anywhere.** It is read in five places
  (`fix_cohort_service`, `fix_applicability_service`, `execution_verification_service`,
  `case_outcome_service`, the materializer) and written nowhere, so `fix_patterns` is
  never populated — which leaves the B4 applicability join, the B5 cohort counters and
  the verification write-back dormant in practice, not merely unexercised.
- **`claim_evidence` and `decision_claims` have no constructors.** Claims are created
  by `claim_service`, but nothing links one to its evidence or to a decision, and
  nothing ever moves a claim past `unverified` — so the validation lifecycle in
  `VALIDATION_STATUSES` is unreachable.

F1 populated eight of the 79 and put the remaining 71 in the register with an owner
each. The register is the durable half: set equality in both directions, so a new
unwritten column fails CI, and a column that gains a writer also fails until its
register entry is removed.

**Precedent, and the reason this is fixable.** The same class of finding was recorded
on 2026-08-05 for a different `0029` table — *"Outcome loop is schema-only (P0-4,
confirmed): `CaseOutcome` / `CaseStateTransition` have model definitions and no writer
anywhere"* (`KNOWN_GAPS.md:17`). That one has since been closed:
`services/case_outcome_service.py:113,161,239` now writes all three outcome models.
So the pattern is known, the fix shape is proven, and `KNOWN_GAPS.md:17` is itself now
stale. The execution and decision governance columns are the remaining instance —
which is what F1 exists to finish and F1's guard test exists to prevent recurring.

#### O-2 · The action-policy table is not in the enforcement path

`ActionPolicy` / `DecisionActionPolicy` are read by exactly two consumers —
`graph/agent/materializer.py:262-278` and `graph/agent/hydrators.py:47` — both
part of the **agent projection**. `services/execution_service.py` never queries
them; gating comes from `TenantPolicy` via `approval_policy_service`. There is
also no CRUD API for action policies, so rows arrive only via seed scripts.

So the "policy/control plane" is really two disconnected halves: an enforced
approval policy (rich, fail-closed, narrow) and a projected action policy
(broad vocabulary, no engine). The review's 85% conflates them.

#### O-3 · Approval integrity is weaker than 60%

Confirmed absent: `requestedArtifactVersion`, `requestedArtifactHash`, policy
snapshot on the approval, `expiresAt` on a *granted* approval, and any
pre-execution re-check. There is no content hash on `PlaybookVersion` at all,
and `PlaybookVersion.steps` is mutable JSONB — so "the exact artifact that was
approved" is not currently expressible, let alone verifiable.

What *does* exist: `services/approval_expiry_service.py` expires **pending**
requests after 72h and never approves on expiry (`:27-56`). That is the E6
safety slice, and it is good — but it is request-staleness, not approval-validity.

Two defects surfaced while checking this:

- `approval_expiry_service.py:55` writes `status = "expired"`, which is not in
  `APPROVAL_STATUSES` (`models/execution.py:13`) and has no CHECK constraint
  behind it. Harmless today, a trap for the next reader.
- Nothing prevents a published `PlaybookVersion.steps` payload from being
  mutated in place after approval. Immutability is convention, not constraint.

#### O-4 · `ExecutionAttempt` is missing entirely — and the review never mentions it

v6 §26.2 makes retries first-class: `attemptNumber`, `idempotencyKey`,
`deduplicationKey`, `inputHash`, `workerRef`, plus a `DEDUPLICATED` result state.
ContextEdge has one `ExecutionStepRun` row per step with a single status. There
is no retry, no cancellation, no resume — corroborated by
`services/approval_expiry_service.py:10-12` and `KNOWN_GAPS.md:123`
("tool registry, rollback execution, cancellation and resume remain Release-2
scope").

#### O-5 · Rollback is ~10%, not 50-60%

The only rollback artifacts are free text: `PlaybookVersion.rollback_notes`
(`models/playbook.py:152`) and `PlaybookStep.rollback_hint`
(`schemas/playbook.py:51`). `PlaybookRollbackRequest` (`schemas/playbook.py:309`)
rolls back a *playbook version*, not an executed action. There is no
`RollbackPlan` / `RollbackAction` / `RollbackExecution` / `RollbackVerification`,
and `reversible` on a step is a flag nothing consumes.

#### O-6 · Escalation is ~30%, not 65-70%

Grep for escalation across `backend/src/contextedge` returns prompts, an
`escalate_to_human` decision type (`models/decision.py:16`), and an `escalated`
case status (`models/case_outcome.py:38`). There is no `Escalation` entity, no
`escalatedTo`, no evidence bundle, no acknowledgement timestamps — so v6 §28's
central requirement ("the human should receive the evidence bundle and
rejected/attempted alternatives") is unmet.

#### O-7 · "Document versioning" persists nothing

`services/documents/versioning.py` is a filename/hash heuristic producing
in-memory dataclasses (`DocumentIdentity`, `DuplicateGroup`) — version markers
parsed from `v2` / `rev3` / `SOP (2).docx`, plus a qualifier rank for
draft/final/latest. There is no `KnowledgeVersion` table, no `effectiveFrom`/
`effectiveTo`, and no persisted supersession: `superseded_by` edges are written
only for **claims** (`graph/agent/materializer.py:199-200`). The module's own
docstring (`:15`) names the problem it does *not* yet solve — retrieval "returns
superseded guidance and nothing marks it as superseded".

#### O-8 · Verification is one criterion family, and it infers success from silence

`execution_verification_service` checks exactly two negative signals — new
incident threads and new alert batches on the run's CIs after completion — and
returns `verified` when neither fires. There is no positive-signal criterion
(metric recovered, synthetic transaction, ticket state, user confirmation), no
per-criterion observation record, no per-step verification, and no
`ROLLBACK_REQUIRED` / `MONITOR_REQUIRED` / `PARTIAL_SUCCESS` assessment states.
A CI that simply stopped emitting telemetry reads as `verified`.

The honest score is ~45% of v6 §27, not 80% — though the *closed loop* itself
(verdict → cohort counters → fix patterns) is genuinely shipped and rare.

#### O-9 · Correlation evidence taxonomy is thin

Only two `correlation_type` values are ever written — `case_link_match` and
`identity_match` (`services/correlation_service.py:473-482`). v6 §9.3 lists
thirteen correlation evidence types. The *mechanism* is strong (the cluster
resolver's per-member `reasons` carry much of the rest), but typed, queryable
correlation evidence is narrower than 95% implies.

### 4.3 Gaps the review did not find

| # | Gap | Evidence |
|---|---|---|
| G-1 | **Policies are unversioned.** `ActionPolicy` has no `version` / `effective_from` / `effective_to`; `DecisionActionPolicy.policy_result_snapshot` records a result with no policy version. v6 §23.2 requires `policyVersion` + `artifactHash` on every `PolicyCheck`; that is currently unsatisfiable. | `models/action_policy.py:54-115,145-147` |
| G-2 | **Knowledge validation is not wired into retrieval.** `knowledge_validation_service` computes support levels; `knowledge_retrieval_service` ranks on distance × `rank_penalty` only. v6 §16.5's "a stale KB SHOULD be penalized during retrieval" is not implemented. | `services/knowledge_retrieval_service.py:294` |
| G-3 | **Model/prompt provenance never reaches the derived entity.** `prompt_version` and model flow into `llm.usage` operational events, but the payload carries no episode/decision/claim id — reconstructing "which prompt version produced this episode" requires a correlation-id join, and only when a `db` session was passed. | `ai/observability.py:213-237` |
| G-4 | **`edge_type` is unvalidated free text, written from 26 modules.** Central helpers already exist (`add_edge` / `ensure_edge` / `close_edge` / `replace_edge`), so validation is a small, high-leverage change — but today the MAF allowlist governs *reads* only. | `graph/builder.py:15-211`; `graph.builder` imported by 26 modules under `backend/src/contextedge` |
| G-5 | **`DecisionTrace` anchors to the case, not the episode.** `Decision.session_id` → `resolution_sessions`; v6 §22 anchors to `Episode`. A cross-walk is needed before any v6 export claims conformance. | `models/decision.py:55-60` |
| G-6 | **There is no executor and no write-capable agent tool.** All six MAF tools are read-or-propose: `query_context_graph`, `get_cohort_shared_attributes`, `propose_dependency`, `cmdb_topology`, `assess_change_risk`, `assess_fix_applicability`. `execution_service` is a ledger/state machine; `record_tool_invocation` is called *by* an external caller. | `integrations/maf/tools.py`; `services/execution_service.py:459` |
| G-7 | **Two external specifications now compete.** The branch already conformed to the *AE Ops Context Graph design* (Section 43.x, migration `0029`). v6 is a second, differently-named ontology covering the same ground. Adopting both verbatim guarantees drift. | `codewiki/17-ae-ops-context-graph-alignment.md` |
| G-8 | **ANN indexing has a deployment caveat.** `0032` fails loud below pgvector 0.7, but environments stamped at an earlier revision of that file never re-execute it and stay on sequential scans. `docker-compose.yml` pins `pgvector/pgvector:pg16`. | `alembic/versions/0032_halfvec_hnsw_indexes.py:34-38` |

---

## 5. Revised scorecard

"Wired" = a service writes it and something consumes it. Where the review and
this document agree, the row is omitted for brevity — the full agreement list is
Section 4.1.

| v6 capability | Review | Revised | Basis for the change |
|---|---:|---:|---|
| Temporal validity | 78% | **75%** | Valid-time is real; system-time absent. Fair as scored. |
| Identity resolution | 95% | **90%** | Decisions are events, not entities; Layer 2 records nothing. |
| Episode correlation | 95% | **85%** | Mechanism strong; typed correlation evidence is 2 of 13 kinds. |
| Assertion / qualified fact | 75% | **60%** | `Claim` has no subject/predicate/object, no validity window, no source ref. |
| ResolutionObservation | 80-85% | **60%** | Facts exist across 4 tables; no version scope, no verification link, no evidence weight. |
| KB / SOP / document knowledge | 90% | **70%** | Validation and applicability are strong; no persisted `KnowledgeVersion` or supersession. |
| Remediation plans | 80% | **70%** | Playbook versions are good; no artifact hash, no blast radius, steps are mutable JSONB. |
| ResolutionAction | 80% | **55%** | `action_name` / `action_type` unpopulated; no action registry. |
| Skill model | 65% | **35%** | `tool_ref` is a free string; no registry, no interface type, no I/O schema. |
| ExecutionContract | 60-65% | **10%** | Nothing exists. Explicitly Release-2 scope. |
| Immutable approval snapshot | 60% | **20%** | No hash, no version binding, no expiry on grant, no pre-execution re-check. |
| Scoped TrustProfile | 30% | **20%** | No model. Raw material exists (cohort stats, calibration buckets, verification verdicts). |
| Execution lifecycle | 90% | **60%** | Good ledger; no attempts, retries, cancellation, resume, or live idempotency. |
| Verification | 80% | **45%** | One criterion family; success inferred from absence of failure. |
| Rollback | 50-60% | **10%** | Free text only. |
| Escalation | 65-70% | **30%** | No entity, no evidence bundle, no acknowledgement. |
| Policies / control plane | 85% | **55%** | Enforced approval policy is real; action policy has no engine and no versioning. |
| Relationship semantics | 80% | **70%** | Read-side allowlist only; write side unvalidated across 29 modules. |
| Provenance | 95% | **70%** | Source lineage excellent; model/prompt provenance not on derived rows. |
| Storage / search | 95%+ | **90%** | True at head, with the `0032` deployment caveat (G-8). |

**Composite.** Capability coverage of the problem v6 describes: **~78%**
(review: 88%). Production-complete against the *full autonomous* vision:
**~62%** (review: 80-83%). Literal v6 schema conformance: **~45%**
(review: 60-65%).

The revision is not pessimism about the codebase — it is the difference between
scoring a schema and scoring a system. The reading/reasoning half of ContextEdge
is stronger than most systems that claim this problem space. The acting half is
a well-designed ledger waiting for its engine.

---

## 6. Design decisions

**D-1 · Adopt v6 as a semantic contract, not a schema to migrate to.**
*Why:* the review's central recommendation is right, and the code proves it —
`shadow` mode, `partially_validated_fix`, the seven-level applicability ladder
and reviewer-gated cohort promotion are all *more* operationally specific than
v6's abstractions. Rewriting toward v6's class names would trade working
machinery for nomenclature.
*Tradeoff:* a mapping layer is a second artifact that can rot. It needs an owner
and a test, or it becomes a stale glossary — which is exactly what happened to
the `0029` column set.

**D-2 · One canonical mapping document, not two external specs.**
*Why:* G-7. The branch already carries an AE Ops alignment article keyed to
"Section 43.x". Adding v6 vocabulary alongside it creates two names for every
concept and two definitions of done.
*Tradeoff:* consolidating means re-editing `codewiki/17`, and some AE Ops
section numbers will lose their referent. Worth it; the alternative is that
every future review re-litigates which spec applies.

**D-3 · Delete-or-populate every unwired column, and enforce it with a test.**
*Why:* an unwritten column is worse than a missing one — it makes reviewers
(human and AI) score capability that does not exist, which is precisely how this
review reached 88%. The repo already has a drift-guard test
(`tests/test_review_orm_ddl_drift.py`) that could carry the rule.
*Tradeoff:* some columns were provisioned deliberately ahead of a feature, and a
strict rule forces either premature implementation or an explicit allowlist
entry. The allowlist is the point: "provisioned, unwired, owner: F6" is a
truthful state; silence is not.

**D-4 · Sequence governance work behind the executor, not ahead of it.**
*Why:* G-6. With no write-capable tool, an artifact-hash binding built today
would be tested against nothing and would drift before its first real use. The
correct trigger is "before the first tool that mutates a customer system", not
"before the next release".
*Tradeoff:* if a write-capable tool is added opportunistically — a single MAF
tool that calls a remediation API — it will arrive *before* the governance, and
the temptation will be to ship it "just for one low-risk action". Mitigate with
a hard gate: no tool that performs a side effect merges until F6+F7+F8 are in.

**D-5 · Do not build an RDF store; if semantic export is ever needed, project it.**
*Why:* agrees with the review, with one addition — a read-only JSON-LD/SHACL
projection over the existing tables gets every claimed benefit (interoperability,
machine-checkable shapes, external validation) at a fraction of the cost, and
can be validated in CI over fixtures rather than at runtime.
*Tradeoff:* a projection cannot enforce shapes at write time, so SHACL becomes a
CI signal rather than a database constraint. Given that Postgres CHECK
constraints and Pydantic gates already do write-time enforcement, that is the
right split — but it means "SHACL-validated" must be stated as "validated at
export", not "enforced at ingest".

**D-6 · If the semantic layer is built, align to PROV-O + NORIA-O rather than v6's `example.org` namespace.**
*Why:* v6 §4 itself says production deployments SHOULD replace `example.org`.
NORIA-O is a published, maintained ITSM ontology (Orange, BSD-4-Clause, ESWC
2024, and an active IETF NMOP draft dated 2026-08-07) covering resources,
events, trouble tickets, problem categories and remediation actions, already
aligned with SEAS/BOT/UCO/SLOGERT. Minting a private namespace forfeits that
for nothing.
*Tradeoff:* NORIA-O's coverage of *decision/approval/execution governance* is
thinner than v6's — so the export would be NORIA-O + PROV-O for the observed
world plus a ContextEdge-controlled namespace for the decision graph. Two
namespaces, but only one of them ours to maintain.

---

## 7. Improvement plan — Epic F

Slots into `codewiki/BACKLOG.md` after Epic E, following the existing working
agreement (stacked branch per item, three review-fix-review passes, full suite,
CI green, KNOWN_GAPS + codewiki updated on merge).

Two milestones. **M7 makes the system honest about itself** — cheap, no
prerequisites, and it removes the exact ambiguity that produced this review's
inflated score. **M8 is the governed-autonomy prerequisite set** — nothing in it
ships value on its own, and all of it must land before the first side-effecting
tool.

### M7 — Truthful governance *(≈3 items S/M, 1 item M)*

**F1 · Populate or retire the `0029` stub columns — M**
Decide each column: write it at its natural point, or drop it. Minimum
population set: `ExecutionStepRun.action_name` + `action_type` + `execution_mode`
+ `executed_by` (from the step payload and run mode at `start_execution`),
`ApprovalRequest.action_name` + `approver_role` + `recommended_by`,
`Decision.decision_intent` + `policy_result` + `risk_level`. Add a guard test
that fails when a model column has no writer and no allowlist entry with an
owning backlog item.
*Acceptance:* the guard test is red before the change and green after; every
remaining unwired column appears in the allowlist with an owner.
*Why this first:* it is the difference between a schema and a system, and every
later item's acceptance criteria depend on these fields being real.

**F2 · Relationship type registry — S**
A canonical `EDGE_TYPES` registry validated inside `graph/builder.add_edge` /
`ensure_edge`. A test asserts (a) every type written anywhere is registered, and
(b) every registered type is either in `MAF_RELATIONSHIP_TYPES` or carries an
explicit exclusion reason — the `mentions_identity` fan-out note in
`profiles.py:120-126` is the model for that reason string.
*Acceptance:* an unregistered `edge_type` raises; the projection allowlist and
the write registry cannot silently diverge.

**F3 · Policy versioning + a real `PolicyCheck` record — M**
Add `version`, `effective_from`, `effective_to` to `action_policies`; record each
evaluation as a row carrying policy id + version + evaluated artifact ref +
result + input snapshot + evaluator + timestamp. Extend
`DecisionActionPolicy.policy_result_snapshot` or supersede it.
*Acceptance:* "which policy version evaluated this decision, and what did it
see?" is answerable by query for every gated decision.
*Depends on:* F1 (`Decision.policy_result` must be written).

**F4 · Knowledge freshness and supersession in retrieval — M**
Persist the `knowledge_validation_service` support level per knowledge evidence
item; add support level + evidence recency as ranking terms in
`knowledge_retrieval_service`; turn the `versioning.py` heuristic into
reviewer-gated `superseded_by` edges between knowledge evidence rows.
*Acceptance:* an article with `failing` support or a superseding successor ranks
below its replacement for the same query; the reviewer queue shows proposed
supersessions; nothing is auto-applied (the `IdentityMergeProposal` pattern).

**F5 · Provenance closure on derived entities — S/M**
Put the target entity type + id on `llm.usage` events, and record
`prompt_name` / `prompt_version` / model on the derived row (episodes,
decisions, claims) — a small JSONB `generation_provenance` column is enough.
*Acceptance:* "which prompt version and model produced this episode" is one
query, with no correlation-id join and no dependence on whether a `db` session
was in scope.

### M8 — Governed autonomy prerequisites *(gate: no side-effecting tool merges until F6–F8 are in)*

**F6 · Skill registry + `ExecutionContract` — L**
Promote `PlaybookStep.tool_ref` from free string to a registry reference.
`Skill`: id, name, version, action type, interface type
(API/MCP/RPA/CLI/SCRIPT/WORKFLOW/MANUAL), input/output JSON Schema, reversible,
rollback skill ref, risk level, allowed principals, status.
`ExecutionContract`: idempotency mode (NATIVE / CALLER_KEY / DEDUPE_ONLY /
NOT_IDEMPOTENT), dedup window, timeout, retry policy, max attempts, backoff,
cancellation support, dry-run support, side-effect classification, concurrency
policy, rate limit, credential scope.
*Acceptance:* an executable step cannot publish without resolving to a
registered skill; a skill with side effects cannot register without a contract;
`shadow` mode is expressed as the contract's dry-run path rather than a special
case in `start_execution`.
*Note:* the existing `SAFETY_CLASSES` tuple maps cleanly onto v6's
`sideEffectClassification` — reuse it rather than minting a parallel vocabulary.

**F7 · Immutable approval binding — M**
Canonicalize the resolved step payload with RFC 8785 (JSON Canonicalization
Scheme) and hash it. Store `artifact_version`, `artifact_hash`, `policy_snapshot`
and `expires_at` on the approval; re-hash and compare immediately before
execution; refuse on mismatch or expiry with a distinct error. Add the CHECK
constraint that makes `PlaybookVersion.steps` immutable after publish, and add
`expired` to `APPROVAL_STATUSES` (O-3).
*Acceptance:* mutating an approved step payload by one character blocks
execution; an expired approval blocks execution; both emit operational events.
*Why RFC 8785:* key ordering, whitespace and number formatting all change bytes
without changing meaning — a naïve `json.dumps` hash produces false mismatches
on re-serialization and false matches on nothing. JCS is the IETF standard for
exactly this.

**F8 · `ExecutionAttempt` + live idempotency — M/L**
An attempts table (attempt number, skill + version, idempotency key, dedup key,
input hash, worker ref, started/completed, status incl. `DEDUPLICATED`,
`TIMEOUT`, `CANCELLED`). Generate and enforce the idempotency key so
`uq_execution_step_runs_idempotency_key` stops being decorative; set
`duplicate_check_status` on every attempt.
*Acceptance:* replaying an execution request with the same key produces a
`DEDUPLICATED` attempt and zero new side effects; v6 §42 invariant 8 holds.

**F9 · Generalized verification — L**
`VerificationCriterion` / `VerificationObservation` / `VerificationAssessment`.
Keep today's two signals as criterion types (`incident_absence`,
`alert_absence`), and add at least one positive-signal type (ticket state or
user confirmation — both are already available from the connectors). Assessment
states: SUCCESS / PARTIAL_SUCCESS / FAILED / INCONCLUSIVE / ROLLBACK_REQUIRED /
MONITOR_REQUIRED / ESCALATE_TO_HUMAN.
*Acceptance:* a run against a CI with no telemetry cannot return `verified` on
silence alone — it returns `INCONCLUSIVE`; each verdict lists the criteria that
produced it.
*Sequenced before F10 deliberately:* trust scores computed from a
silence-equals-success verifier would be systematically inflated.

**F10 · Scoped `TrustProfile` — L**
Scope key: agent × action type × resource class × environment × business
criticality × tenant. Metrics: sample size, success rate, verification pass
rate, rollback rate, human override rate, reopen rate, recent failure rate, and
a **Wilson score lower bound** (not a raw ratio — 3 successes out of 3 must not
outrank 340 out of 350). Autonomy verdict: ADVISORY / SUPERVISED / AUTONOMOUS /
SUSPENDED, consumed by the control decision alongside policy.
*Acceptance:* the v6 §25 worked example resolves correctly — high-sample
restart on a non-critical Windows service reaches AUTONOMOUS while a 3-sample
Oracle failover on a payment service stays SUPERVISED, and a recent failure
streak demotes without a deploy.
*Reuse:* this is structurally the same machine as B5's cohort statistics +
reviewer-gated promotion ladder (`services/fix_cohort_service.py`,
`models/fix_cohort.py`) applied to (agent, action) instead of (fix, CI class).
Build it as a sibling, not a novel subsystem.

**F11 · Rollback and escalation objects — M**
`RollbackPlan` / `RollbackAction` / `RollbackExecution` linked to the forward
execution; `Escalation` with reason, escalated-by/to, priority, decision trace
ref, **evidence bundle ref**, recommended next actions, and acknowledgement
timestamps.
*Acceptance:* a failed verification can produce a rollback execution with its
own verification; an escalation hands a human the evidence bundle and the
rejected alternatives, not a notification string.

### Deliberately deferred (Epic F backlog tail)

| Item | Why deferred |
|---|---|
| Structured `Assertion` alongside `Claim` | Real v6 gap, but pure modelling gain until something queries subject/predicate/object. Revisit when the agent needs "what was asserted about X, by whom, valid when". |
| Canonical `ResolutionObservation` projection | The facts exist across `CaseOutcome`, `CaseOutcomeFixPattern`, `FixCohortStat` and `ExecutionRun.verification_status`. Ship as a read-model/API first; a table only if the read-model proves insufficient. |
| System-time (bitemporal) history | Postgres 16 has no native system versioning (application-time constraints arrived in 18; more in 19), so this means an append-only history table or the `periods` extension. Defer until a real "what did we believe on date X" requirement appears — `operational_events` already answers most audit forms of the question. |
| JSON-LD / SHACL export | Only when an external consumer asks. Then per D-5/D-6: read-only projection, PROV-O + NORIA-O alignment, SHACL in CI over fixtures. |

### Recommended order

1. **M7**: F1 → F2 → F5 → F3 → F4 *(F1 first; F3 depends on it, F2/F5 are independent fillers)*
2. **M8**: F6 → F7 → F8 → F9 → F10 → F11 *(contract before hash; hash before attempts; verification before trust)*
3. Deferred tail: revisit at the next external review, or when a consumer appears.

```text
/goal implement backlog milestone M7 (F1 populate-or-retire the 0029 stub columns
with a no-writer guard test, F2 relationship type registry validated in
graph/builder, F5 generation provenance on derived entities, F3 policy versioning
+ PolicyCheck records, F4 knowledge freshness and supersession in retrieval) from
codewiki/BACKLOG.md, one stacked branch per item, 3 review-fix-review passes,
CI-verified merge each
```

```text
/goal implement backlog milestone M8 (F6 skill registry + ExecutionContract,
F7 RFC-8785 immutable approval binding, F8 ExecutionAttempt + live idempotency,
F9 generalized verification criteria, F10 scoped TrustProfile with Wilson lower
bound, F11 rollback + escalation objects) from codewiki/BACKLOG.md; no
side-effecting tool merges until F6-F8 are in; 3 review passes per item
```

---

## 8. Acme VPN incident (this layer)

Acme Corp's VPN authentication failure after Windows update KB5032190 —
`vpn-gw-east-01` failing `AUTH_CERT_EXPIRED`, reported by `jsmith@acme.com`
across `JIRA-4521`, `JIRA-4522`, a Teams thread and a root-cause email.

**What works today.** The four sources cluster into one episode: the ticket
numbers bridge Jira to the Teams messages as case memberships, the identity tier
correlates on `jsmith@acme.com` and `vpn-gw-east-01`, and the cluster resolver
keeps the 30-day window from dragging in last quarter's certificate ticket. The
episode names KB5032190 as trigger and cert renewal as resolution; a pattern
forms; a playbook is generated with evidence refs; `vpn-gw-east-01` resolves to
its CI class, so the applicability ladder can judge whether the same fix
transfers to `vpn-gw-west-02`. If a human runs the playbook and the run
completes, the verification sweep re-checks 30 minutes later for new incidents
or alerts on the gateway and writes `verified` — which feeds the fix cohort.

**What breaks under Epic F's absence.** Suppose the fix becomes automated.
Today: the approver sees "renew certificate on vpn-gw-east-01" and approves;
nothing binds that approval to the exact step payload, so an edit between
approval and execution goes unnoticed. The step has no contract, so there is no
timeout, no retry policy and no dry-run. Retrying after a timeout creates no
attempt record and trips no duplicate check — the idempotency column stays NULL.
If the renewal half-works, verification returns `verified` because the gateway
went quiet, when in fact it stopped emitting. And the decision to let an agent
do this at all rests on a global automation mode, not on "this agent has renewed
417 certificates on this device class in production with a 0.4% reopen rate".

**After M8.** The approval carries `sha256` of the canonicalized step payload
and expires in 4 hours. The renewal skill declares `idempotencyMode: CALLER_KEY`,
a 90-second timeout, two attempts with backoff, and `REVERSIBLE_WRITE` with a
rollback skill. The retry is attempt 2 of the same request, deduplicated against
the same key. Verification requires a positive signal — a successful synthetic
authentication against `vpn-gw-east-01`, not merely the absence of complaints —
and returns `INCONCLUSIVE` rather than `verified` if the gateway is silent. The
trust profile for (agent, `renew_certificate`, `vpn_gateway`, production,
business-critical) has enough verified samples and a high enough Wilson lower
bound to permit supervised execution, and demotes itself the first time a
renewal reopens.

---

## 9. External grounding

- **Canonical hashing (F7).** [RFC 8785 — JSON Canonicalization Scheme](https://www.rfc-editor.org/info/rfc8785/)
  is the IETF standard for producing byte-identical JSON for hashing and
  signing: object keys sorted by UTF-16 code unit, whitespace stripped, numbers
  normalized per ECMAScript. Without it, re-serializing an approved payload
  changes the hash without changing the meaning. Practical walkthrough:
  [Connect2id — securing JSON objects with HMAC](https://connect2id.com/blog/how-to-secure-json-objects-with-hmac).
- **Bitemporality on Postgres (deferred item).** Native temporal support is
  arriving in stages — application-time (valid-time) constraints in
  [PostgreSQL 18/19 temporal tables](https://www.postgresql.org/docs/19/ddl-temporal-tables.html),
  with system versioning still requiring an extension
  ([Temporal Extensions wiki](https://wiki.postgresql.org/wiki/Temporal_Extensions),
  [temporal_tables](https://pgxn.org/dist/temporal_tables/)). ContextEdge runs
  `pgvector/pgvector:pg16`, so any system-time work today is hand-rolled —
  another reason to defer it until a requirement names it.
- **Reusable ITSM ontology (D-6).** [NORIA-O](https://github.com/Orange-OpenSource/noria-ontology)
  (Orange, BSD-4-Clause) models IT resources, events, trouble tickets, problem
  categories and remediation actions in RDF/OWL/SKOS, aligned with SEAS, BOT,
  UCO and SLOGERT; published at [ESWC 2024](https://2024.eswc-conferences.org/wp-content/uploads/2024/04/146640354.pdf)
  and carried into an active IETF draft,
  [draft-tailhardat-nmop-incident-management-noria-05](https://datatracker.ietf.org/doc/draft-tailhardat-nmop-incident-management-noria/)
  (2026-08-07, individual submission, no standards-track status).
- **Autonomy governance context (F10).** The industry gap this closes is well
  documented — [VentureBeat, on agents gaining autonomy faster than evaluation
  can verify them](https://venturebeat.com/orchestration/enterprise-ai-is-entering-an-evaluation-gap-agents-are-gaining-autonomy-faster-than-companies-can-verify-them),
  and [AURA, an agent autonomy risk assessment framework](https://arxiv.org/pdf/2510.15739).
  Both argue the same thing v6 §25 does and this plan sequences: autonomy is
  granted per scope against measured evidence, not per agent against a
  reputation.

---

## 10. Code map

| Concern | Module path | Key symbols | When it runs |
|---|---|---|---|
| Identity resolution + decisions | `services/identity_service.py` | `resolve_extracted_entities`, `_record_resolution_decision`, `_adjudicate_candidates` | Extraction, per evidence item |
| Merge proposals | `services/identity_reconciliation_service.py`, `models/episode.py` | `IdentityMergeProposal` | Scheduled reconciliation pass |
| Episode clustering | `services/episode_cluster_service.py` | `resolve_episode_cluster`, `EpisodeCluster` | Debounced reconstruction |
| Correlation | `services/correlation_service.py` | `correlate_evidence_item`, `create_correlation` | Post-normalization |
| Graph writes | `graph/builder.py` | `add_edge`, `ensure_edge`, `close_edge`, `replace_edge` | Every edge writer (26 importing modules) |
| Temporal traversal | `graph/temporal.py` | `edge_valid_at`, `normalize_graph_as_of` | Graph queries with `as_of` |
| Agent projection | `graph/agent/profiles.py`, `materializer.py`, `hydrators.py` | `MAF_RELATIONSHIP_TYPES`, `MAF_V1`, `FIX_RESULT_EDGE_TYPES` | MAF tool calls, 6h materializer beat |
| Execution ledger | `services/execution_service.py` | `start_execution`, `request_approval`, `decide_approval`, `record_tool_invocation` | API-driven |
| Approval policy | `services/approval_policy_service.py` | `load_approval_policy`, `check_decider`, `check_automation_mode` | Execution start + approval decision |
| Approval expiry | `services/approval_expiry_service.py` | `expire_stale_approvals` (72h) | Verification beat |
| Verification | `services/execution_verification_service.py` | `verify_execution_run`, `_post_action_signals`, `_confirm_alert_batches` | 15-minute beat |
| Cohort learning | `services/fix_cohort_service.py`, `models/fix_cohort.py` | `record_fix_outcome`, `FixCohortStat` | From verification verdicts |
| Applicability | `services/fix_applicability_service.py`, `models/fix_applicability.py` | `APPLICABILITY_LEVELS`, `FixApplicabilityRule` | MAF tool + API |
| Knowledge validation | `services/knowledge_validation_service.py` | support levels (`unproven`/`emerging`/…) | Reviewer surfaces |
| Knowledge retrieval | `services/knowledge_retrieval_service.py` | ranking with `rank_penalty` | Playbook generation |
| Document identity | `services/documents/versioning.py` | `DocumentIdentity`, `DuplicateGroup` | Attachment ingest |
| Governance stubs (unwired) | `models/execution.py`, `models/decision.py`, `models/action_policy.py` | `idempotency_key`, `sod_check_status`, `decision_intent`, `policy_result` | **Never — see O-1** |

---

## 11. Further reading

- [`codewiki/17-ae-ops-context-graph-alignment.md`](17-ae-ops-context-graph-alignment.md) — the first external alignment; its "What's Deliberately Not in 0029" table is the origin of O-1
- [`codewiki/BACKLOG.md`](BACKLOG.md) — epics A–E and milestones M1–M6; Epic F above continues it
- [`codewiki/KNOWN_GAPS.md`](KNOWN_GAPS.md) — execution engine depth (line 123) corroborates O-4/O-5
- [`codewiki/10-governance-sessions-execution-audit.md`](10-governance-sessions-execution-audit.md) — the execution ledger as designed
- [`codewiki/16-decision-traces.md`](16-decision-traces.md) — decision model, options, outcomes
- [`codewiki/07-episodes-patterns-playbooks.md`](07-episodes-patterns-playbooks.md) — the learning loop this plan extends
- [`codewiki/12-identity-resolution-and-thread-hydration.md`](12-identity-resolution-and-thread-hydration.md) — the layered resolver behind §4.1
