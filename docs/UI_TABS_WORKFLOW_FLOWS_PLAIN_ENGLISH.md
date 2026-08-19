# ContextEdge UI Tabs - Plain English Workflow Flows

This document explains the workflow flow for every UI tab. Use it in demos when someone asks, "How does this tab work in the pipeline?"

Each flow names the function or Celery task that does the work, so you can click through and read it. Verified against the code on **2026-08-19**.

The running example everywhere is the **Acme VPN incident**: ServiceNow incident `INC0010427`, "VPN tunnel flapping on `vpn-gw-east-01`", with a Teams thread beside it and an engineer's email quoting the ticket number. Root cause: an expired VPN gateway certificate. Fix: renew the certificate and restart the tunnel service.

## Full System Flow

```text
Sources
  -> Sync Operations
  -> Evidence
  -> Correlations / Identities
  -> Episodes
  -> Patterns
  -> Playbooks
  -> Runtime
  -> Sessions
  -> Review Queue
  -> Execution
  -> Decisions
  -> Audit Log

Governance and safety around the flow:
Negative Knowledge, Review Queues (Suggestions), Contradictions, Drift,
Evaluations, Policies, LLM Cost, Pipeline Health, Settings, Graph Explorer
```

**Simple demo line:**
Data enters from Sources, becomes Evidence, related Evidence becomes Episodes, repeated Episodes become Patterns, Patterns create Playbooks, Runtime recommends a Playbook, a human reviews it, Execution records the approved action, and Decisions/Audit Log record what happened.

## The queue map behind all of it

```text
sync        -> sync.run_backfill / sync.run_incremental_sync
extraction  -> extraction.normalize_evidence, artifact.extract_attachment
default     -> extraction.classify_relevance (fast lane), maintenance.*, identity.*
hydration   -> hydration.hydrate_thread
correlation -> extraction.correlate_evidence, extraction.reconstruct_episode,
               extraction.compute_evidence_baseline
embedding   -> extraction.chunk_evidence, extraction.embed_chunks_batch
pattern     -> pattern.cluster_episodes, pattern.generate_playbook_candidate,
               pattern.deduplicate_knowledge
evaluation  -> evaluation.* (drift, contradictions, verification, AI review,
               retention, graph reconciliation, suggestions)
```

Routing rules are matched in order at `backend/src/contextedge/workers/celery_app.py:226`. The eight queues are listed in `backend/dev.py:16`.

Why the split matters: `correlation` and `embedding` were carved out of `extraction` after a measured incident. Correlation tasks were dispatched but never received behind 8,255 queued normalizations, so Episodes, Patterns and Playbooks all sat at zero; and 1,879 chunks existed with only 15% embedded, meaning evidence was ingested and silently unretrievable. **A worker fleet that does not consume all eight queues reproduces exactly that.**

On Windows, the topology is two worker groups: several `-P solo` processes for the high-volume lanes and one `-P solo` worker for `sync,pattern,evaluation`, plus exactly one beat process. Prefork does not work on Windows, and `-P threads` breaks the LLM client's event-loop-bound locks. See docs/RUNBOOK.md "Worker topology".

## 1. Overview

```text
Four plain list calls in parallel (/sources, /evidence, /episodes, /playbooks)
  -> Overview dashboard
  -> User sees system status
```

**Used for:** First health check.

**Note:** these are page-limited lists, not database-wide counts (`frontend/src/app/(dashboard)/overview/page.tsx:110`). For real pipeline numbers use Pipeline Health.

**Example:** An Acme admin opens Overview and sees evidence count increased, but the ServiceNow source has not synced.

## 2. Sources

```text
Admin creates source
  -> POST /api/v1/sources (api/v1/sources.py:80) stores config + Fernet-encrypted credentials
  -> POST /api/v1/sources/{id}/discover (sources.py:204)
     -> discover_source_objects (services/source_service.py:87)
     -> connector.discover_objects() -> upsert SourceObject rows
  -> operator approves an object for backfill/sync on the Entity Inventory screen
  -> Source is ready for backfill/sync
```

**Used for:** Define where data comes from.

**Key point:** discovery does not pull data. Nothing syncs until `approved_for_backfill` / `approved_for_sync` is set on the object (`backend/src/contextedge/models/source.py:55`).

**Example:** Add ServiceNow for Acme, discover `incident` / `problem` / `change_request` / `kb_knowledge`, approve `incident`.

## 3. Sync Operations

```text
Beat: sync.trigger_scheduled_syncs every 900s (workers/sync_tasks.py:14)
  -> one sync.run_incremental_sync per approved object
     -> run_incremental_job (services/sync_worker_service.py:526)
        -> acquire_sync_lock  (advisory lock; second worker = skipped_locked)
        -> load newest SyncCheckpoint; none -> skipped_no_checkpoint, stop
        -> connector.fetch_changes(checkpoint)
        -> persist_ingestion_events (services/ingestion_persistence.py:19)
             - dedupe on (tenant, source, external_id, content_hash)
             - payload > 32KB -> MinIO, DB keeps a stub
        -> commit
        -> queue_normalize_raw_objects -> one normalize_evidence per raw id
```

**Used for:** Monitor data import jobs.

**Backfill** is the same shape with a date window (default 90 days) and the `approved_for_backfill` gate (`sync_worker_service.py:419`).

**If the hand-off fails after the commit**, the un-queued raw ids are parked on `source_objects.metadata_extra["pending_normalize_raw_ids"]`, the run is marked failed, and the next successful run drains them (`sync_worker_service.py:322`). Nothing is lost.

**Pause / cancel** is cooperative: the connector reads the signal between pages and every 25 detail records, on a fresh connection so it can see the operator's write. Everything already fetched is persisted with its checkpoint (`services/sync_control_service.py:97`).

**Example:** A Jira sync fails because the token expired. Sync Operations shows the failed run; the checkpoint was not advanced, so nothing is skipped when it is fixed.

## 4. Evidence

```text
raw_evidence_objects row
  -> extraction.normalize_evidence (workers/extraction_tasks.py:1304)
     body = _normalize (extraction_tasks.py:122):
       1. load payload (from MinIO if offloaded)
       2. noise gate for hydrated messages (services/message_filter.py:81)
          - delivery_failure / quote_only / empty / coordination_only -> NO row
       3. title + body extraction; content hash on the RAW body
       4. redaction (services/redaction_service.py:36)
       5. dedupe on (tenant_id, content_hash)
          - hit -> refresh facets / case_state / knowledge_state, stop
       6. insert EvidenceItem with derived evidence_type, knowledge_state,
          case_state, source_facets
       7. relevance classification  [LLM #1]
       8. skip gate: not_relevant AND confidence >= 0.75 -> stop enrichment
       9. message-function classification (chat/email only)  [LLM #2]
      10. deterministic error-signature fingerprints (no LLM)
      11. identity resolution  [LLM #3 family]
      12. decision extraction  [LLM #4]
      13. parent embedding
      14. chunk dispatch (inline if body < 16KB, else async)
  -> after commit: correlate_evidence + compute_evidence_baseline,
     or attachment extraction, or thread hydration
```

**Used for:** Store searchable facts.

**Important code behavior:** the noise gate is deterministic and runs *before* any model call - it rejects about 47% of hydrated messages on live data, and the raw object is kept so a rule change can re-judge them exactly. The content hash is taken on the raw body before cleaning and redaction, so tuning those rules never breaks deduplication.

**Chunking** then splits the body for search (`services/evidence_chunk_service.py:43`). The chunker is chosen by record shape first, then source: a KB article goes to the document chunker, a ticket to the ticket chunker, chat/email to the thread chunker (`services/chunkers/registry.py:116`). Chunks are embedded in batches of 32 on the `embedding` queue (`workers/chunk_tasks.py:238`); until that lands, the item is findable only through its parent embedding and full-text search.

**Example:** The Acme ServiceNow ticket, the engineer's email, and four Teams messages become evidence. "Any update on the VPN?" dies at the noise gate. "Restarted IPSec on vpn-gw-east-01, tunnel stable" survives - it is short, but it carries a hostname.

## 5. Sessions

```text
One live issue starts
  -> POST /api/v1/sessions (api/v1/sessions.py:45)
  -> Add symptoms/entities/context
  -> review_queue.prefetch_review_context warms the reviewer cache
  -> Runtime matches, decisions and execution runs attach to the session
  -> every retrieval/decision appends a decision_trace_events row
     (services/session_service.py:139)
  -> Session becomes the case file
```

**Used for:** Track one problem from start to finish.

**Example:** "VPN tunnel flapping on vpn-gw-east-01" becomes one session.

## 6. Runtime

```text
User enters symptoms/entities
  -> POST /api/v1/runtime/match (api/v1/runtime.py:89)
     -> build_runtime_memory_context (services/memory_service.py:82)
        short-term: session + last 5 trace events + recent evidence
        long-term:  resolved identities, approved playbook / pattern counts
        reasoning:  last 3 execution runs, last 5 decisions
     -> risk cap from caller roles (runtime.py:42)
     -> rank_playbooks (search/hybrid_ranker.py:213)
        - approved playbooks with a published version only
        - FTS pass + one attributed query embedding
        - per playbook: semantic (over that version's evidence), graph,
          identity, quality, freshness, negative penalty
        - abstain if every score < 0.35
     -> append_trace_event("retrieve") on the session
     -> cache the explain payload in Redis for 1 hour
```

**Used for:** Find the best playbook for a current issue.

**Technical rule:** weights are keyword 0.25, semantic 0.30, graph 0.15, evidence quality 0.10, identity 0.05, recency 0.10, freshness 0.05, minus a negative penalty of 0.05 (`search/hybrid_ranker.py:22`). The semantic score is gated by the keyword score, so vector similarity alone cannot carry a playbook whose words never appear in the query.

**Example:** Symptoms mention tunnel flapping, IKE re-negotiation and a certificate error on `vpn-gw-east-01`. Runtime returns "Renew VPN gateway certificate and restart the tunnel service".

## 7. Review Queue

```text
System proposes an action / a step needs approval
  -> pending decisions listed by confidence, deduped one-per-session
  -> GET /api/v1/review-queue/{session_id}/context (api/v1/review_queue.py:30)
     one bundled read: session, top pending decision, similar-decision
     aggregate, scoped decisions, execution runs, recent events (Redis cached)
  -> Reviewer approves / modifies / rejects
     - approve  -> POST /execution/runs/{run}/approvals/{id}/decide
     - modify   -> POST /execution/runs/{run}/approvals/{id}/modify
     - reject   -> POST /decisions/{id}/reject
  -> approval policy evaluated at decide time
     (services/approval_policy_service.py:127)
  -> policy_checks row written for allow AND deny
```

**Used for:** Human approval before trusting important AI/system actions.

**Why the policy check matters:** the row is keyed to the policy **version**, so editing the policy later cannot rewrite what a past run was judged under. The denial path is recorded too - that is the evaluation an audit trail usually loses.

**Example:** The reviewer approves the certificate renewal and rejects the "fail over to the secondary gateway" alternative.

## 8. Execution

```text
Approved playbook version
  -> POST /api/v1/execution/runs (api/v1/execution.py:65)
     -> start_execution: automation-mode cap, per-step action policy,
        trust check, approval requests created for gated steps
  -> external runner calls back per step:
     POST /runs/{run}/steps/{step}/invocations (execution.py:135)
       - approval re-checked against the step's content hash
       - duplicate side-effecting step refused, recorded, not replayed
     POST /runs/{run}/steps/{step}/complete   (execution.py:179)
  -> POST /runs/{run}/complete  (refuses while steps are open)
  -> evaluation.verify_executions (every 900s) re-checks the CIs after
     the playbook's recheck_after_sec (default 1800, floor 300)
```

**Used for:** Run or track approved playbook actions safely.

**Say this out loud in a demo:** ContextEdge has **no executor** on this branch. Every agent tool is read-only or propose-only, and `execution_service` is a governed ledger driven by an external caller (`codewiki/KNOWN_GAPS.md:34`). The controls are real; the runner is not in this repo.

**Verification does not treat silence as success.** Absence of new incidents counts only when that CI has produced incidents or alerts in the last 30 days; otherwise the verdict is `unverifiable` (`services/execution_verification_service.py:56`).

**Example:** "Restart the IPSec tunnel service on vpn-gw-east-01" is a side-effecting step, so it waits for approval, then is recorded as invoked and completed, then verified 30 minutes later.

## 9. Decisions

```text
Runtime, a reviewer, or an agent chooses an action
  -> create_decision (services/decision_trace_service.py:51)
     - decision_intent derived from decision_type
     - risk_level taken from the SELECTED option only
     - decisions row + decision_options rows
     - graph edges: based_on, considered, chose, applied_policy, followed_by
     - decision_trace_events row on the session
     - inline embedding for "similar past decisions"
  -> record_outcome later flips status pending -> completed
```

**Used for:** Explain what was decided and why.

**Example:** The decision records that failover was rejected because it masks the certificate expiry and the fault returns within a day, and renewal was selected.

## 10. Episodes

```text
correlate_evidence created edges
  -> extraction.reconstruct_episode, countdown 180s (correlation_tasks.py:39)
     body = _reconstruct (workers/extraction_tasks.py:995), gates in order:
       a. resolve_episode_cluster (services/episode_cluster_service.py:108)
          connected component over case_links + correlation_edges,
          max 50 members, max 3 hops, 30-day window from nearest seed,
          legal-hold and pending-redaction rows fenced out in SQL
       b. cluster < 3 members            -> skipped_below_min_cluster
       c. optional resolution gate (off by default)
       d. per-cluster advisory lock      -> skipped_locked
       e. debounce settle re-check       -> deferred_unsettled
          (starvation guard: narrate anyway after 1800s)
       f. same cluster fingerprint       -> duplicate_cluster
       g. growth < 50% over prior draft  -> skipped_insufficient_growth
       h. resolve each item's source role (ticket / working_discussion /
          external_communication / document / monitoring)
       i. supersede smaller pending drafts
       j. create_episodes_from_evidence (services/episode_service.py:114)
          -> reconstruct_episode  [LLM]
          -> validate_episode schema gate
          -> episodes + episode_steps + episode_evidence_links + embedding
  -> human approves, or the hourly AI review sweep does
  -> approval dispatches evaluation.extract_issue_signature
     and pattern.cluster_episodes per domain
```

**Used for:** Convert scattered evidence into one incident story.

**Important code behavior:** at most 20 evidence items go into one LLM call, each truncated to 2,000 characters (`ai/extractors/episode_extractor.py:44`, `:48`). Evidence is labelled `[ev-N]` and the model must cite those labels per episode and per step; labels it invents are dropped, so it cannot mint evidence (`episode_extractor.py:77`).

**AI review** (`services/episode_review_service.py:174`) has exactly three modes - `off`, `advisory`, `auto_approve`. Advisory stamps a verdict on `episodes.ai_review` and approves nothing. Auto-approve also requires deterministic floors: at least 2 evidence items, an outcome of 20+ characters, verdict `approve`, confidence at least 0.8. An auto-approved episode keeps `reviewer_user_id` NULL, so it is always distinguishable from a human approval. The reviewer re-reads the row `FOR UPDATE` after the model call, so a concurrent human decision always wins.

**Example:** The ServiceNow ticket, four Teams messages and the engineer's email become one episode: "VPN tunnel flapping on vpn-gw-east-01 - expired gateway certificate."

## 11. Patterns

```text
Episode approved (single, bulk, or AI auto-approve)
  -> pattern.cluster_episodes per domain (workers/pattern_tasks.py:379)
     body = _cluster (pattern_tasks.py:127):
       0. repair missing episode embeddings
       1. candidates = approved + embedded + not already in a pattern,
          this domain scope only, LIMIT 100
       2. for each candidate:
          a. existing pattern within cosine distance < 0.35?
             -> validate_pattern_match  [LLM] -> add to that pattern
          b. else group neighbours within cosine distance < 0.20
             (an empty group is allowed - a single-episode cluster)
          c. synthesize_pattern  [LLM]  -> create_pattern_from_episodes
             - domain-safety assertion on every member
             - patterns row + pattern_evidence_links + enrichment edges
             - auto-enqueue pattern.generate_playbook_candidate
       3. dedup sweep rides along at the end
```

**Used for:** Find repeated problems.

**Technical rule:** two different distances. `< 0.35` decides "does this episode belong to an existing pattern" (then a model call adjudicates); `< 0.20` decides "which episodes group into a new pattern".

**There is no beat schedule for clustering.** It is dispatched by episode approval or run manually from the Patterns tab. If nobody approves episodes, no patterns form.

**Degradation to know:** the adjudication call fails **open** - during a provider outage it returns `is_match=True` at 0.75, so the 0.35 embedding probe alone decides membership (`ai/extractors/pattern_extractor.py:108`).

**Example:** Three certificate-expiry episodes across VPN and RADIUS become one "gateway certificate expiry causes tunnel flapping" pattern.

## 12. Playbooks

```text
Pattern created or grown
  -> pattern.generate_playbook_candidate (workers/pattern_tasks.py:403)
     - skip if a playbook already exists for this pattern or title
     - skip if pattern.confidence < 0.5
     - collect up to 12 episode summaries (with real ids so [ep-N] resolves)
     - collect up to 20 negative-knowledge lines
     - retrieve_knowledge_for_pattern  [embedding + search]
       (services/knowledge_retrieval_service.py:226)
     - persist_knowledge_links: pattern -[supported_by]-> evidence
       for documents at similarity >= 0.75
     - generate_playbook_candidate  [LLM, prompt playbook v5]
     - validate_source_refs   -> drop invented citations
     - classify_step_grounding -> uncited steps forced to best_practice
     - risk floor from step safety classes (LLM may only raise it)
     - no steps -> fail the task, do not create an empty playbook
     - Playbook (candidate, suggest_only) + PlaybookVersion 0.1.0
     - embed_playbook, references_identity and derived_from edges
  -> human reviews the lifecycle -> approved playbook reaches Runtime
```

**Used for:** Turn repeated problems into reusable fix steps.

**How knowledge is chosen** (`services/knowledge_retrieval_service.py:291`): oversampled semantic search, then keep only `kb_article` / `sop` / `documentation`; **withhold** anything the source system marked draft, in review, or retired - a human retired it, so ranking it last would override that decision; then re-rank (never filter) by empirical support, applicability, and supersession; keep the top 5 documents with up to 6 sections each. Warnings travel into the prompt rather than hiding the article.

**The manual route is different.** `POST /playbooks/generate` skips knowledge retrieval, the confidence floor, the risk floor, the empty-steps guard, and playbook embedding, and it drops every `ep-N` citation because it does not pass episode ids. Use the worker path when demonstrating grounded generation.

**Example:** The pattern creates "Renew VPN gateway certificate and restart the tunnel service", and the retrieved SOP contributes a "back up the current certificate first" step cited as `[kb-1]`.

## 13. Negative Knowledge

```text
Bad/risky action is known
  -> POST /api/v1/negative-knowledge (api/v1/negative_knowledge.py:36)
  -> consumed in two places:
     - hybrid ranker subtracts score
       (search/hybrid_ranker.py:140, contradicts edges + domain count)
     - up to 20 entries enter the playbook generation prompt
       (workers/pattern_tasks.py:494)
```

**Used for:** Remember what not to do.

**Example:** "Do not fail over to the secondary VPN gateway before checking certificate expiry - failover hides the expiry and the fault returns within a day."

## 14. Identities

```text
Evidence text (title + body + first 2000 chars of payload, re-redacted)
  -> extract_identities  [LLM, prompt identity v3, input fenced as untrusted]
  -> normalize_extracted_entity (services/identity_normalizer.py:81)
     - a single-token device name matching the hostname pattern becomes
       a strong hostname identifier (identity_normalizer.py:134)
  -> resolve_extracted_entities (services/identity_service.py:616)
     layer 1  strong identifier exact match          -> 1.00
     layer 2  typed exact alias                      -> 0.95
     (candidacy gate rejects non-names and facet-shaped values here)
     layer 3  LLM adjudication, <= 5 candidates
              auto-link only at >= 0.95 (person) / 0.90 (other),
              otherwise a NEW identity marked needs_review
     layer 4  unmatched -> provisional identity      -> 0.50
  -> evidence_identity_links + canonical_entity_refs + mentions_identity edges
  -> promote_corroborated_identities: provisional -> resolved once >= 2
     distinct evidence items cite it (and <= 5, the rarity guard)
  -> daily identity.reconcile_identities PROPOSES merges >= 0.95 confidence
```

**Used for:** Understand that different names can mean the same real thing.

**Nothing merges automatically.** The daily pass files a proposal in `identity_merge_proposals` and a human decides on the Identities tab; rejections are durable so the pair is never re-raised.

**Example:** "vpn-gw-east-01", "VPN-GW-EAST-01" and "vpn-gw-east-01.acme.local" become one device identity, resolved deterministically at layer 1 after the first sighting.

## 15. Correlations

```text
New evidence committed
  -> extraction.correlate_evidence (workers/correlation_tasks.py:16)
     -> correlate_evidence_item (services/correlation_service.py:197)
        tier 1  deterministic case links, confidence 1.0
                (own id, thread id, ServiceNow problem/change/parent refs,
                 Jira issue links, quoted ticket numbers)
        tier 2  identity co-occurrence, gated:
                - 7-day window, fail-closed on missing timestamps
                - identities with 200+ links carry no signal
                - rare non-person entity 0.75, common 0.65, +0.1 for 2+
                - a single shared PERSON is dropped entirely
                - conflicting-ticket veto: both sides already in different
                  cases -> delete the correlation
        enrichment (each in its own SAVEPOINT, fail-soft):
                ServiceNow / Jira / SapphireIMS / Zoho reference services,
                ticket-number bridging, reply inheritance, thread topics
  -> if edges were created: schedule reconstruct_episode (countdown 180s)
```

**Used for:** Connect evidence records that belong together.

**Order does not matter.** A quoted ticket number that has not been ingested yet is stored as a pending mention and reconciled the moment the ticket registers.

**Example:** The ServiceNow ticket, the Teams thread and the engineer's email link at 1.0 through `INC0010427`. A separate Teams thread that only names `vpn-gw-east-01` in the same week correlates at 0.75, because the device is a rare entity.

## 16. Review Queues (Suggestions)

```text
evaluation.generate_correlation_suggestions (after chunk embeddings land)
  -> correlation_suggestions rows, status pending
evaluation.detect_fleet_groups (every 1800s)
  -> fleet_group_suggestions rows, status pending
identity resolution abstained or fell below threshold
  -> canonical_identities with resolution_state = needs_review

  -> /suggestions page lists all three
  -> reviewer accepts  -> the edge is written / the identity is resolved
  -> reviewer rejects  -> durable; never re-raised
```

**Used for:** Deciding the cases the machine deliberately refused to decide.

**Nothing here has been applied to the graph yet.** Requires `knowledge_manager`, `domain_admin` or `tenant_admin`.

**Example:** A suggestion proposes linking a firewall change record to the Acme VPN incident on chunk-embedding proximity. The reviewer accepts, and the episode cluster grows.

## 17. Graph Explorer

```text
Writers (ingest, correlation, patterns, decisions) call ensure_edge
  (graph/builder.py:50 - SELECT, then INSERT ... ON CONFLICT DO NOTHING)
  require_registered refuses any edge type outside the 69-type registry
evaluation.reconcile_graph_relationships every 6h projects relational rows
  into edges (graph/agent/materializer.py:54) - idempotent, additive only

  -> User selects node type / id / depth / domain
  -> GET /graph/neighbors -> bounded BFS, max depth 3 (graph/queries.py:20)
     GET /graph/subgraph  -> capped at 250 nodes / 500 edges
  -> UI shows connected records

Agent-facing variant:
  POST /graph/agent-subsets -> seeds -> traversal -> budget -> hydration
  (graph/agent/repository.py:156, selector.py:28, hydrators.py:98)
```

**Used for:** See relationships between sessions, evidence, episodes, patterns, playbooks, decisions, users, and actions.

**Two things to state accurately:** node visibility is fail-closed per type in the agent projection - an unapproved playbook or a retired KB article silently disappears - but the plain `/graph/neighbors`, `/graph/subgraph` and `/graph/stats` routes filter by tenant only, which is a known open item (`codewiki/KNOWN_GAPS.md:56`). And agent-proposed dependencies land as a non-traversable `proposed_depends_on` edge at confidence 0.3 until a reviewer promotes them.

**Example:** Open the Acme VPN session graph and see evidence, episode, pattern, playbook, decision, approval and execution run connected.

## 18. Contradictions

```text
Beat: evaluation.scan_contradictions_task every 12h (evaluation_tasks.py:88)
  -> scan_contradictions (services/contradiction_service.py:318)
     for each approved playbook's latest published version:
       extract step texts
       -> top 20 KB/SOP/documentation evidence items closest to the
          step embedding (contradiction_service.py:206)
       -> should_compare_contradiction: require >= 2 shared meaningful
          tokens (1 is allowed only for a very short fragment)
       -> _llm_confirms_contradiction  [LLM, prompt contradiction v1]
       -> contradictions row created
       -> scan state recorded so unchanged pairs are not re-checked
  -> Human reviews and sets status
```

**Used for:** Catch conflicting knowledge.

**Why the token gate exists:** a single-word overlap let a step "must disable MFA" match a KB snippet that merely said "MFA". Two shared tokens is the measured floor.

**Example:** The playbook says restart the tunnel service first; a vendor bulletin says restarting before renewing an expired certificate drops all sessions with no benefit.

## 19. Drift

```text
Beat: evaluation.detect_drift every 6h (evaluation_tasks.py:41)
  -> check_playbook_drift (services/drift_service.py:104)
  GET /drift/alerts -> list_drift_alerts (drift_service.py:13), read-only:
     - expiry_at in the past                       -> past_expiry
     - last_validated_at older than 90 days        -> not_validated_in_N_days
     - >= 3 negative retrieval feedback in 30 days -> high_negative_feedback_N
     - source pattern grew after the playbook was generated
  -> Drift alert shown for review
```

**Used for:** Find old playbooks that may no longer be safe.

**Two thresholds, do not mix them up:** Drift **flags** a playbook after 90 days without validation; the Runtime ranker's **freshness score** decays linearly over 180 days and sits at 0 past expiry (`search/hybrid_ranker.py:382`). So a drifting playbook is already being ranked lower before anyone opens this tab.

**Example:** The Acme VPN certificate playbook has not been validated in 180 days and picked up three negative feedback events, so Drift flags it on both counts.

## 20. Evaluations

```text
Create test dataset (api/v1/evaluations.py:60)
  -> POST /evaluations/runs -> evaluation.run_evaluation
     -> replays each case through the SAME rank_playbooks the live
        Runtime tab uses (services/evaluation_service.py:134)
     -> compares actual ranking with the expected playbook
  -> pass/fail and ranking results shown
```

**Used for:** Test if AI/search logic is working correctly.

**Example:** An Acme VPN test case should return "Renew VPN gateway certificate", not "Fail over to the secondary gateway".

## 21. Policies

```text
Admin creates policy (api/v1/policies.py:83)
  -> assign to source / evidence / playbook (policy_assignments.py:119)
     assert_policy_assignment validates type + tenant
  -> enforcement points:
     access      -> excluded from search for non-admins
                    (search/access_control.py:12)
     retention   -> archive window for the daily sweep
     approval    -> checked at start_execution and decide_approval
                    (services/approval_policy_service.py:106, :127)
     action      -> per-step verdict, most_restrictive wins
                    (services/action_policy_service.py)
  -> every evaluation, allow or deny, writes a policy_checks row
     keyed to the policy VERSION
```

**Used for:** Governance and control.

**Version rule:** editing `config` bumps the version; renaming or deactivating does not. The version tracks rules, not labels.

**Example:** Medium-risk production actions require human approval, and self-approval is banned.

## 22. Audit Log

```text
Any mutating POST/PATCH/PUT/DELETE under /api/v1 (login excluded)
  -> RequestAuditMiddleware after the response
     (middleware/request_audit.py:25)
     - always: structlog line http.mutating_request
     - when a tenant resolved: audit_logs row, action
       "http.<method>.<path-slug>", outcome success / denied / failed
Business actions also call log_audit_event explicitly
  (middleware/audit.py:10 - e.g. sync.pause at api/v1/sources.py:354)
Machine-readable stream is separate: append_operational_event
  (services/event_log_service.py:32) - llm.usage, correlation.case_linked,
  episode.ai_approved, and so on
```

**Used for:** Compliance and traceability.

**Note:** the audit insert runs off-thread on its own connection and swallows its own failures, so auditing can never break a request. Unauthenticated 401 probes never resolve a tenant and exist only in the log line.

**Example:** Audit Log records who approved the VPN certificate renewal.

## 23. LLM Cost

```text
Any LLM or embedding call
  -> llm_complete / generate_embedding (ai/provider.py)
     1. check_budget BEFORE spending
        (services/tenant_budget_service.py:234)
        - no budget row -> deployment defaults: 2,000,000 tokens/day,
          $25/day, action block
        - block -> TenantBudgetExceeded raised, nothing spent
        - warn  -> proceed + llm.budget_warning event
     2. output-token clamp per task
     3. circuit breaker + timeout + one fallback-model attempt
     4. finally: record_llm_usage - Prometheus, one llm.usage log line,
        one operational_events row (ai/observability.py:133)
  -> GET /admin/llm-usage sums those same events
     (services/admin_cost_service.py:75)
```

**Used for:** Track AI cost and budget limits.

**One source of truth:** the dashboard and the budget gate both sum `llm.usage` events, so there is no second counter to drift. Costs are estimates for dashboard use; the provider's bill is authoritative.

**A blocked tenant fails softly:** each enrichment in `_normalize` is individually wrapped, so evidence still lands - un-embedded and un-linked. The signature is chunks with NULL embeddings plus `llm.usage` events showing `outcome = budget_exceeded`.

**Example:** An Acme admin sees episode reconstruction dominating today's extraction tokens after a backlog import.

## 24. Pipeline Health

```text
GET /api/v1/admin/pipeline-health (api/v1/admin_cost.py:166)
  -> get_pipeline_health (services/pipeline_health_service.py:87)
     - Redis LLEN per queue, in pipeline order
     - HLEN unacked for in-flight work
     - one SQL roll-up: evidence -> embedded -> identities ->
       correlations -> episodes -> patterns -> playbooks
     - backlog alert at depth 500
  -> operator reads the first zero in the chain
```

**Used for:** Finding where the pipeline stopped, not whether one task succeeded.

**Why in-flight matters:** during a reconstruction wave, 5,800 debounced tasks churned for hours in `unacked` while every queue depth read zero.

**Example:** Evidence is climbing, chunks are written, embedded is stuck at 15%, and the embedding queue reads zero - the fleet was started without the `embedding` queue.

## 25. Settings

```text
Admin configures tenant / workspace / domain / users
  -> /tenants, /workspaces, /domains, /users
  -> roles are assigned via POST /users/{id}/roles (api/v1/users.py:111)
  -> login embeds role NAMES in the JWT (api/v1/auth.py:92)
  -> require_role checks the name only (deps.py:37)
```

**Used for:** Organization setup.

**Caveat to state plainly:** `RoleBinding.scope_type` / `scope_id` are stored but not enforced, so a domain admin bound to one domain effectively holds that role tenant-wide. Narrower scope exists only through service-token `allowed_domain_ids` on routes that consult it. The Retention tab is a pointer to the policies API, not a console.

**Example:** An Acme admin creates the "Network Operations" domain and assigns users to it.

## Acme VPN End-To-End Demo Flow

```text
 1. Sources: connect ServiceNow, Teams, Gmail; approve the incident table.
 2. Sync Operations: import INC0010427, the Teams thread, the email.
 3. Evidence: store them; the noise gate drops the "any update?" chatter.
 4. Identities: vpn-gw-east-01 resolves to one canonical device.
 5. Correlations: the ticket, thread and email join one canonical case at 1.0.
 6. Episodes: one incident story with cited steps.
 7. Review Queues: accept the low-confidence firewall-change suggestion.
 8. Patterns: group with two earlier certificate-expiry episodes.
 9. Negative Knowledge: "do not fail over before checking expiry".
10. Playbooks: generate "Renew VPN gateway certificate and restart the
    tunnel service", with the SOP's backup step cited as [kb-1].
11. Runtime: recommend it for the next flapping-tunnel report.
12. Sessions: the live case file for this outage.
13. Review Queue: a human approves the side-effecting step.
14. Execution: record the invocation, completion, and verification verdict.
15. Decisions: store why renewal beat failover.
16. Graph Explorer: show every connected record.
17. Contradictions: flag the vendor bulletin against the old restart-first step.
18. Drift: flag the playbook once it passes 90 days without validation.
19. Evaluations: confirm the same symptoms still retrieve the right playbook.
20. Policies: enforce approval and access rules.
21. Audit Log: record every approval.
22. LLM Cost: show what reconstruction and generation cost.
23. Pipeline Health: the first place to look if any step above produced nothing.
24. Settings: manage domains and users.
25. Overview: overall system health.
```

**One-line demo story:**
For a flapping VPN tunnel on `vpn-gw-east-01`, ContextEdge collects evidence from three systems, links it into one case, builds a readable episode, recognises it as a recurring certificate expiry, generates an evidence-cited playbook, recommends it the next time, waits for a human approval, records the approved action against the exact step that was approved, and keeps the whole trail auditable.
