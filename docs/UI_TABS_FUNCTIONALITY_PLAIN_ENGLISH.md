# ContextEdge UI Tabs - Plain English Functionality Guide

This document explains every main UI tab in simple business language. Each section covers what the tab is for, why it is needed, the main functionality available there, and a practical example.

Checked against the code on **2026-08-19**. Where a claim is load-bearing, the file and line are given so you can click through and confirm it yourself.

**One shared example runs through this guide: the Acme VPN incident.** Acme Corp uses ServiceNow, Teams and email. The corporate VPN starts dropping connections. ServiceNow gets incident **INC0010427**, "VPN tunnel flapping on `vpn-gw-east-01`". A Teams thread runs alongside it while engineers try things. An engineer emails a root-cause note that quotes "INC0010427". The real cause turns out to be an expired VPN gateway certificate; the fix is renewing the certificate and restarting the tunnel service. Every tab below is explained against that one incident, so you can trace a single record end to end.

## Project Logic Sequence For Demo

The sidebar order is the UI navigation order. For explaining the project logic in a demo, use this workflow order instead:

```text
1. Overview
2. Sources
3. Sync Operations
4. Evidence
5. Sessions
6. Runtime
7. Review Queue
8. Execution
9. Decisions
10. Episodes
11. Patterns
12. Playbooks
13. Negative Knowledge
14. Identities
15. Correlations
16. Review Queues (Suggestions)
17. Graph Explorer
18. Contradictions
19. Drift
20. Evaluations
21. Policies
22. Audit Log
23. LLM Cost
24. Pipeline Health
25. Settings
```

**Simple explanation:**
Overview shows the current health of the system first. Sources define where data comes from, Sync Operations imports it, Evidence stores it, Sessions handle one live issue, Runtime suggests the best action, Review Queue lets a human approve or reject, Execution records approved actions, and the governance tabs keep everything safe and auditable.

**Short demo line:**
Overview shows status. Sources bring data. Sync imports it. Evidence stores it. Session opens a case. Runtime suggests a fix. Review Queue gets human approval. Execution tracks the approved action. Decisions and Audit Log record what happened.

**One thing to say out loud early in any demo:** the screens are thin. Almost all the work happens in background jobs (Celery tasks) on eight queues. If a tab looks empty, the usual cause is that a worker is not consuming its queue, not that the API is broken. The Pipeline Health tab exists for exactly that question.

## 1. Overview

**What this tab is used for:**
The Overview tab is the main health snapshot of ContextEdge. It shows whether data is flowing into the platform, how much evidence has been collected, how many episodes and playbooks exist, and where attention may be needed.

**Why this tab is needed:**
Users need one starting page where they can quickly understand whether the system is healthy. Without this page, an operator would need to open Sources, Evidence, Episodes, and Playbooks separately just to know the current status.

**Main functionality:**

- Shows high-level counts for sources, evidence, episodes, and playbooks.
- Highlights ingestion health and freshness signals.
- Helps users decide which area needs attention first.
- Acts as the landing page for daily monitoring.

**How it actually works:**
There is no dedicated overview API. The page fires four ordinary list calls in parallel - `/sources`, `/evidence`, `/episodes`, `/playbooks` (`frontend/src/app/(dashboard)/overview/page.tsx:110-113`). So the numbers are "first page of that list", not database-wide totals. For real pipeline numbers, use Pipeline Health.

**Example:**
An Acme admin logs in and sees evidence rising overnight but the ServiceNow source not synced since yesterday. They click into Sources or Sync Operations to investigate.

## 2. Sources

**What this tab is used for:**
The Sources tab manages all data sources connected to ContextEdge, such as local files, tickets, chat systems, email, ServiceNow, Jira, Zoho Desk, ManageEngine, SapphireIMS, or other operational tools.

**Why this tab is needed:**
ContextEdge can only learn from operational evidence if it knows where to collect that evidence from. This tab is the control point for setting up and managing those connections.

**Main functionality:**

- Lists configured data sources.
- Adds new sources. The picker is generated from the connector registry, so a registered connector can never be missing from it (`backend/src/contextedge/connectors/registry.py:24`).
- Shows source type, sync mode, and status.
- Opens source details for policy assignment, credential rotation, discovery, and recent sync runs.
- Pauses, resumes or cancels a running sync (`backend/src/contextedge/api/v1/sources.py:295`).
- Supports local folder ingest for demos or offline data imports.

**What is live today:**
Seven connectors are real: Teams, Gmail, ServiceNow, Jira Service Management, ManageEngine SDP, SapphireIMS, and Zoho Desk (`backend/src/contextedge/connectors/registry.py:91`). Confluence, SharePoint and Exchange appear in the catalog marked `planned` only.

**Where approval happens:**
Discovery finds objects; it does not start pulling data. Approving an object for backfill or scheduled sync happens on the Entity Inventory screen reached from the source detail page. Nothing syncs until that switch is on.

**Example:**
An Acme admin connects ServiceNow, discovers the `incident`, `problem`, `change_request` and `kb_knowledge` tables, and approves `incident` for backfill. Later, INC0010427 arrives through that path.

## 3. Sync Operations

**What this tab is used for:**
Sync Operations monitors background data collection jobs, backfills, retries, and failed sync items.

**Why this tab is needed:**
Source setup is not enough. Users also need to know whether data is actually being pulled successfully. This tab helps troubleshoot ingestion failures before they affect search, episodes, or playbooks.

**Main functionality:**

- Shows sync run history with type (discovery / backfill / incremental), status, and item counts.
- Displays job status, failures, and retries.
- Helps users identify stuck or failed ingestion jobs.
- Supports cleanup actions for old or failed sync records.

**Three behaviours worth knowing before you debug one:**

- **Only one sync per object at a time.** A second worker takes a Postgres advisory lock, fails to get it, and returns `skipped_locked` rather than racing the checkpoint (`backend/src/contextedge/services/sync_worker_service.py:379`).
- **An incremental run with no checkpoint does nothing.** It completes as `skipped_no_checkpoint` with the message "run a backfill for this object first" - deliberately, so a schedule never triggers a surprise full pull (`sync_worker_service.py:571`).
- **Pause and cancel are cooperative and never lose data.** The connector checks the signal between pages and every 25 detail records; whatever was already fetched is persisted with its checkpoint (`backend/src/contextedge/services/sync_control_service.py:15`).

**Example:**
A Jira source was added yesterday, but no new tickets appear in Evidence. The admin opens Sync Operations and sees that the sync job failed because the credential expired.

## 4. Evidence

**What this tab is used for:**
The Evidence tab is the searchable library of raw and normalized operational records collected from all sources.

**Why this tab is needed:**
Every decision, episode, pattern, and playbook must be backed by real evidence. This tab lets users inspect the source material instead of trusting summaries blindly.

**Main functionality:**

- Searches and browses evidence across all sources.
- Shows evidence title, type, relevance, source, and ingest time.
- Opens evidence details with provenance, thread summary, attachments, and context.
- Supports deleting or bulk deleting evidence when allowed.
- Supports thread hydration when a thread is incomplete.
- Lets an admin attach an access policy to a single evidence item.

**What happens to a record on its way into this tab** (all inside `_normalize`, `backend/src/contextedge/workers/extraction_tasks.py:122`):

1. The raw payload is loaded. Payloads over 32 KB live in MinIO object storage, not in the database - the row keeps a small stub (`backend/src/contextedge/services/ingestion_persistence.py:16`).
2. Hydrated chat/email messages pass a deterministic noise gate first. Delivery failures, quote-only replies, empty bodies and pure coordination chatter ("any update?") are dropped with no evidence row and no model call at all (`backend/src/contextedge/services/message_filter.py:81`). On live data this rejects about 47% of messages.
3. Title and body are extracted, and the content hash is taken on the **raw** body before any cleaning, so tuning a regex never breaks deduplication.
4. Sensitive strings are redacted - tokens, JWTs, emails, phone numbers, card numbers, AWS keys, private keys (`backend/src/contextedge/services/redaction_service.py:36`). Everything downstream reads the redacted text.
5. If the same content already exists, the row is **refreshed, not duplicated**: facets, resolved/cancelled state and knowledge state are re-derived, because closing a ticket or retiring an article does not rewrite its body.
6. A relevance classifier labels the item. Only a confident `not_relevant` (confidence >= 0.75) skips the expensive extraction that follows.
7. Identities and decisions are extracted, an embedding is written, and the body is split into chunks for search.

**Search, in one line:** typing a query runs full-text search over a generated `search_tsvector` column, with a ticket-number fallback so `INC0010427` is findable by its number (`backend/src/contextedge/search/pg_fts.py:50`). Semantic search runs separately, matching **chunks** rather than whole documents, then rolling up to one hit per evidence item.

**Example:**
An Acme analyst searches "VPN gateway authentication failure" and gets the ServiceNow incident, three Teams messages, and the engineer's email - each shown once, with the snippet from whichever chunk actually matched.

## 5. Sessions

**What this tab is used for:**
Sessions track governed investigation or resolution work for a specific live case.

**Why this tab is needed:**
When users work on a real incident, the platform needs to remember the context, decisions, evidence, and actions taken. Sessions provide that structured case workspace.

**Why this is important in a demo:**
This shows that ContextEdge does not treat every recommendation as a loose chat message. Each issue has a proper case file where the problem, related systems, decisions, approvals, and final outcome are stored together.

**Main functionality:**

- Lists resolution sessions.
- Creates new sessions for active issues.
- Shows session status, case details, and decision traces.
- Links decisions and evidence to the session.
- Supports closing sessions when work is complete.

**What a session holds mechanically:**
Every retrieval, decision and outcome appends a `decision_trace_events` row plus an operational event (`backend/src/contextedge/services/session_service.py:139`). Creating a session also warms the reviewer cache in the background so the Review Queue opens fast (`backend/src/contextedge/workers/review_queue_tasks.py:33`).

**Example:**
An Acme engineer opens a session for "VPN tunnel flapping on vpn-gw-east-01". The session records the diagnosis, the recommended action, the approval, the execution result, and the final outcome.

**Simple demo explanation:**
Session is the case file for one issue. Everything about the Acme VPN outage is stored inside one session.

## 6. Runtime

**What this tab is used for:**
Runtime is a diagnostic screen for testing the live playbook retrieval system.

**Why this tab is needed:**
When an incident happens, downstream tools and agents call runtime APIs to find the best playbook. This tab lets admins and engineers test the same behavior directly from the UI.

**Main functionality:**

- Sends symptoms and entities to the live retrieval ranker.
- Returns ranked playbook matches.
- Shows match confidence and a full score breakdown.
- Fetches explain details for a match while they are cached.
- Allows users to inspect published runtime playbook versions.
- Records and shows retrieval feedback.

**How the ranking works** (`backend/src/contextedge/search/hybrid_ranker.py:213`):
The ranker only considers **approved** playbooks that have a published version, filtered by domain and by a risk cap derived from the caller's roles. Each candidate gets a weighted score from seven signals - keyword 0.25, semantic 0.30, graph 0.15, evidence quality 0.10, identity 0.05, recency 0.10, freshness 0.05 - minus a negative-knowledge penalty (`hybrid_ranker.py:22`). The semantic signal is deliberately gated by the keyword signal, so pure vector similarity cannot carry a playbook whose words never appear in the query.

**It is allowed to say nothing.** If every candidate scores below 0.35, Runtime returns an empty list and logs `ranking.abstained` (`hybrid_ranker.py:168`). "No recommendation" is a supported answer, not a failure.

**Two operational notes:** the explain payload is cached in Redis for one hour, so `explain` 404s after that; and the query embedding is budget-gated and attributed to the tenant, unlike some internal call sites.

**Example:**
An engineer enters "VPN tunnel flapping, IKE re-negotiation, certificate error" with entity `vpn-gw-east-01`. Runtime returns "Renew VPN gateway certificate and restart the tunnel service" as the top match, with the breakdown showing which signal carried it.

## 7. Review Queue

**What this tab is used for:**
The Review Queue is where humans review important AI-generated or system-generated decisions before they become trusted actions.

**Why this tab is needed:**
ContextEdge uses AI to reconstruct incidents, recommend decisions, and support automation. For safety and governance, high-impact decisions should not be accepted blindly. This tab gives experts a place to approve, reject, or modify recommendations.

**Why this is important in a demo:**
This proves that ContextEdge is not allowing AI to take important actions by itself. The system can suggest the best action, but a human reviewer still controls whether the action is accepted, changed, or rejected.

**Main functionality:**

- Lists pending decisions ranked by confidence, then deduped so one case appears once (`frontend/src/app/(dashboard)/review/page.tsx:122`).
- Shows decision context, rationale, similar past decisions, execution runs, and recent events - all in **one** bundled call (`backend/src/contextedge/api/v1/review_queue.py:30`), read-through cached in Redis.
- Allows reviewers to approve a decision or a pending execution approval.
- Allows reviewers to reject a decision with structured reasons.
- Allows reviewers to modify a proposed action before approval.

**What the gate actually checks:**
Approving is not just a status flip. The approval policy for that playbook is evaluated at decide time - allowed approver roles, the self-approval ban, the minimum safety class that needs approval, and the maximum automation mode (`backend/src/contextedge/services/approval_policy_service.py:12`). Every evaluation, including denials, writes an append-only `policy_checks` row keyed to the policy **version**, so a later policy edit cannot rewrite the history of what a run was judged under.

**Example:**
For the Acme VPN case the system recommends renewing the certificate during the change window rather than failing traffic over to the secondary gateway. A reviewer reads the evidence, agrees that a failover would hide the expiry rather than fix it, and approves the renewal.

**Simple demo explanation:**
Review Queue is the human approval screen. The AI suggests what should happen next, but the human makes the final decision.

- Approve means "Yes, continue."
- Modify means "Change the action first, then continue."
- Reject means "No, this suggestion is wrong."

## 8. Execution

**What this tab is used for:**
Execution manages and records governed playbook runs and the human approvals attached to their steps.

**Why this tab is needed:**
Some actions are safe to suggest but risky to execute automatically. Execution creates a controlled approval layer and a permanent ledger around those actions.

**Main functionality:**

- Starts and lists execution runs.
- Lists pending execution approval requests.
- Shows action details, risk, and requested inputs.
- Lets approved users allow, deny, or modify execution requests.
- Records each tool invocation and step completion.
- Creates an audit trail for automation decisions.
- Shows post-action verification status.

**Say this precisely in a demo: ContextEdge does not run remediation itself.** There is no executor on this branch. `execution_service` is a governed ledger that an external runner drives through the HTTP routes, and all the agent tools are read-only or propose-only (`codewiki/KNOWN_GAPS.md:34`). What is real, and worth showing, is the safety chain around it: a step's approval is bound to a content hash of the exact step version that was approved and re-checked before the call; a duplicate side-effecting step is refused rather than replayed; and each attempt is recorded separately.

**Verification is honest about silence.** After a run completes, a sweep re-checks the affected CIs on the playbook's own schedule (default 30 minutes). Absence of new incidents only counts as success when that CI has actually produced incidents or alerts in the last 30 days; otherwise the verdict is `unverifiable`, not `verified` (`backend/src/contextedge/services/execution_verification_service.py:56`).

**Example:**
The Acme playbook step "restart the IPSec tunnel service on vpn-gw-east-01" is marked as having a side effect, so it requires approval. The approver allows it. Thirty minutes later the verification sweep looks for new incidents and alerts on that CI and records the verdict on the run.

## 9. Decisions

**What this tab is used for:**
Decisions show the reasoning trail behind recommendations, approvals, rejections, and selected actions.

**Why this tab is needed:**
For trust and auditability, users must be able to see not just what the system decided, but why it decided it.

**Main functionality:**

- Lists decision records.
- Shows decision status, intent, confidence, rationale, and risk level.
- Opens decision chains (parents up, children down).
- Shows options considered and the selected option.
- Helps teams inspect why one action was chosen over another.

**Two details that make this trustworthy:**
`risk_level` is taken from the **selected** option only, never from the riskiest option that was considered (`backend/src/contextedge/services/decision_trace_service.py:86`). And every decision gets an embedding at creation time, which is what powers "show me similar past decisions" and the aggregated success rate behind it (`decision_trace_service.py:517`).

**Honest note:** the page uses list, detail and chain. The richer `/decisions/{id}/provenance` and `/decisions/effectiveness` endpoints are built and tested but have no screen yet.

**Example:**
For Acme, two options were recorded: fail over to the secondary VPN gateway, or renew the expired certificate in the change window. The Decisions tab shows failover was rejected because it would mask the expiry and re-fail in 24 hours, and renewal was selected.

## 10. Episodes

**What this tab is used for:**
Episodes are reconstructed troubleshooting stories created from related evidence. They explain what happened, what was tried, what worked, and what the final outcome was.

**Why this tab is needed:**
Raw evidence is often messy. A ticket may have many comments, while the real fix may be hidden inside a chat thread. Episodes turn scattered records into a readable incident timeline.

**Main functionality:**

- Lists reconstructed troubleshooting episodes with their reviewer state.
- Starts episode reconstruction manually from evidence.
- Opens episode details with ordered steps and per-step evidence citations.
- Allows review, approval, bulk approval, and editing of reconstructed episodes.
- Adds or removes evidence from an episode.
- Dispatches an AI review pass over pending drafts.
- Triggers pattern clustering from approved episodes.

**How an episode gets built** (`backend/src/contextedge/workers/extraction_tasks.py:995`):
Reconstruction never narrates a single record. It first materialises the whole connected cluster over case links and correlation edges, bounded to 50 members, 3 hops and a 30-day window from the nearest seed (`backend/src/contextedge/services/episode_cluster_service.py:47`). Then a series of deterministic gates decide whether to spend a model call at all: fewer than 3 members is skipped; a per-cluster lock stops eight workers minting eight identical episodes; a 180-second debounce waits for the cluster to settle, with a 30-minute starvation guard so a never-quiet channel still gets narrated; a duplicate cluster fingerprint is skipped; and a cluster that has not grown by at least 50% over an existing draft is skipped. Each of those gates exists because it was measured - a redundant synthesis costs roughly 12,700 tokens.

**Grounding is enforced, not requested.** The model labels each evidence item `[ev-N]` and must cite them per episode and per step. Labels it invents are dropped, so a model cannot mint evidence that does not exist (`backend/src/contextedge/ai/extractors/episode_extractor.py:77`).

**AI review has exactly three modes** - `off`, `advisory`, `auto_approve` (`backend/src/contextedge/config.py:185`). Advisory stamps a verdict onto `episodes.ai_review` and approves nothing. Auto-approve additionally requires deterministic floors: at least 2 evidence items, a real outcome of 20+ characters, verdict `approve`, and confidence at least 0.8 (`backend/src/contextedge/services/episode_review_service.py:42`). An auto-approved episode leaves `reviewer_user_id` NULL forever, so it is permanently distinguishable from a human approval.

**Example:**
The ServiceNow incident, four Teams messages, and the engineer's email are reconstructed into one episode: "VPN tunnel flapping on vpn-gw-east-01 - expired gateway certificate". The steps show the complaint, the diagnostics, the failed restart, the certificate check, the renewal, and the confirmation.

## 11. Patterns

**What this tab is used for:**
Patterns show recurring operational issues discovered across multiple episodes or evidence items.

**Why this tab is needed:**
One incident is useful, but repeated incidents reveal a bigger problem. Patterns help teams identify common failures and convert them into reusable knowledge.

**Main functionality:**

- Lists detected operational patterns.
- Shows pattern type, confidence, episode count, and freshness.
- Links patterns to supporting episodes and knowledge.
- Opens pattern details and graph views.
- Can generate a playbook from a pattern.
- Runs a manual clustering pass or a knowledge dedup pass.

**What triggers clustering:**
There is **no scheduled clustering job**. Clustering runs when episodes are approved - from the single approve route, from bulk approve, from the hourly AI-review sweep for each domain that had auto-approvals, or manually from this tab (`backend/src/contextedge/workers/pattern_tasks.py:379` and its callers). If nobody approves episodes, no patterns form.

**Two distances, not one:** an episode joins an **existing** pattern when it is within cosine distance 0.35 of a member and an adjudication call agrees; a **new** cluster is grouped from neighbours within 0.20 (`pattern_tasks.py:201`, `:254`). Clustering is also strictly domain-scoped - a domain pass sees only that domain's episodes - because a pattern is synthesized content that becomes visible through the domain predicate.

**Example:**
ContextEdge finds three past incidents where a VPN or RADIUS certificate expired unnoticed and creates a pattern: "Gateway certificate expiry causes tunnel flapping; the successful fix was renewal plus a service restart, and monitoring the expiry date prevented recurrence."

## 12. Playbooks

**What this tab is used for:**
Playbooks are governed step-by-step procedures for resolving operational problems.

**Why this tab is needed:**
The main goal of ContextEdge is to turn past operational experience into trusted, reusable, evidence-backed actions. Playbooks are the durable output of that process.

**Main functionality:**

- Lists candidate, approved, restricted, deprecated, expired, or retired playbooks.
- Opens playbook details and versions.
- Shows stable key, automation mode, lifecycle state, and risk tier.
- Supports lifecycle transitions such as review, approval, restriction, or retirement.
- Supports version comparison and rollback.
- Generates a candidate playbook from a pattern.

**How a candidate is generated** (`backend/src/contextedge/workers/pattern_tasks.py:403`):
The generator reads four different things and keeps them separate on purpose - the pattern itself, its episodes (what people actually did), retrieved knowledge articles and SOPs (what the documentation says), and negative knowledge (what not to do). Where documentation and practice disagree, the disagreement is written into a `conflicts` field for the reviewer rather than silently resolved.

**Three deterministic guards sit around the model:**

- **Citations are validated.** Only labels that were actually shown to the model can survive; invented ones are dropped and counted.
- **Grounding is structural.** A step with no surviving citation is forced to `best_practice`, whatever the model claimed about itself (`backend/src/contextedge/ai/generators/playbook_generator.py:99`).
- **Risk is policy, not model output.** The step safety classes set a floor; the model's suggested risk tier may only raise it (`pattern_tasks.py:36`).

A response with no steps fails the task instead of creating an empty playbook - a truncated response once produced a "complete" playbook with zero steps.

**Example:**
The certificate-expiry pattern becomes "Renew VPN gateway certificate and restart the tunnel service". The retrieved SOP contributes a "back up the current certificate first" step that no engineer had recorded in a ticket, cited as `[kb-1]`.

## 13. Negative Knowledge

**What this tab is used for:**
Negative Knowledge stores steps, fixes, and approaches that are ineffective, conditional, deprecated, or prohibited.

**Why this tab is needed:**
Good operational memory should remember failures too. This prevents the system and users from repeating actions that did not work or should not be used.

**Main functionality:**

- Lists negative knowledge entries.
- Adds new "do not do this" guidance.
- Edits or deletes existing entries.
- Categorizes entries as ineffective, conditional, deprecated, or prohibited.
- Feeds safer retrieval and recommendations.

**Where it is actually consumed:** in two places, both real. It lowers a playbook's ranking score in Runtime (`backend/src/contextedge/search/hybrid_ranker.py:140`), and up to 20 entries are written into the playbook-generation prompt so new candidates avoid the same step (`backend/src/contextedge/workers/pattern_tasks.py:494`).

**Example:**
For VPN tunnel flapping, the Acme team records: "Do not fail over to the secondary gateway before checking certificate expiry - failover hides the expiry and the problem returns within a day."

## 14. Identities

**What this tab is used for:**
Identities manage canonical entities and aliases found across different evidence sources.

**Why this tab is needed:**
The same person, system, workflow, or service may appear under different names in tickets, logs, and chat. Identity resolution connects those aliases so ContextEdge understands they refer to the same thing.

**Main functionality:**

- Lists canonical identities with their resolution state.
- Shows aliases and linked evidence.
- Allows users to merge duplicates.
- Reviews and decides machine-proposed merges.
- Allows users to edit identity metadata.
- Improves search, correlation, graph links, and episode reconstruction.

**How a name becomes an identity** (`backend/src/contextedge/services/identity_service.py:616`), in four layers, cheapest first:

1. **Strong identifier** - an email, hostname, FQDN, IP, serial number or external id matches exactly. Confidence 1.0, no model call. A single-token device name like `vpn-gw-east-01` is recognised as a hostname and resolves this way forever after its first sighting (`backend/src/contextedge/services/identity_normalizer.py:134`).
2. **Typed exact alias** - the same normalised name, scoped to compatible entity types. Confidence 0.95.
3. **Model adjudication** - only for genuinely ambiguous cases, against at most 5 candidates. It links automatically only above the threshold for that type (people need 0.95, everything else 0.9). Below that, or if the model abstains, it creates a **new identity marked `needs_review`** rather than guessing.
4. **Provisional creation** - an unmatched mention becomes a provisional identity at 0.5, promoted to `resolved` once at least two distinct evidence items cite it.

**Nothing merges by itself.** The daily reconciliation pass proposes merges above 0.95 confidence and a human decides; rejections stick so the same pair is never re-raised (`backend/src/contextedge/services/identity_reconciliation_service.py:29`).

**Example:**
"vpn-gw-east-01", "VPN-GW-EAST-01" and "vpn-gw-east-01.acme.local" resolve to one device identity. "Priya Sharma" from the ticket and "Priya" from Teams go to adjudication and only link at 0.95 or above, because people carry the stricter threshold.

## 15. Correlations

**What this tab is used for:**
Correlations manage links between evidence items that are related causally, temporally, semantically, or structurally.

**Why this tab is needed:**
Important incidents often span multiple systems. Correlations connect the dots so ContextEdge can reconstruct the full story.

**Main functionality:**

- Lists correlation edges between evidence records.
- Creates new correlations manually.
- Deletes incorrect correlations.
- Accepts, rejects, splits, or merges correlation decisions.
- Supports investigation, episode reconstruction, and graph building.

**Two tiers do the automatic work** (`backend/src/contextedge/services/correlation_service.py:197`):

- **Deterministic case links at confidence 1.0.** A record's own id, its thread id, ServiceNow reference fields (problem, change, parent incident), Jira issue links, and quoted ticket numbers all resolve into one canonical case. This is order-independent: the email quoting INC0010427 joins the incident's case whether it arrives before or after the ticket.
- **Gated identity co-occurrence.** Two items sharing a resolved identity correlate only within a 7-day window; identities linked to 200+ items carry no signal at all; a rare non-person entity scores 0.75, a common one 0.65; and a single shared **person** is dropped entirely, because people are not incidents.

**A veto exists for the obvious trap:** if both items already belong to different tickets, the identity correlation is deleted and logged - same infrastructure, different incidents (`correlation_service.py:344`).

**Example:**
The ServiceNow incident, the Teams thread and the engineer's email are linked at 1.0 through the ticket number. A separate Teams thread that only mentions `vpn-gw-east-01` in the same week correlates at 0.75, because the device is a rare entity.

## 16. Review Queues (Suggestions)

**What this tab is used for:**
This is the "the machine is not sure" inbox. It holds three kinds of proposal waiting for a human: semantic correlation suggestions, fleet-group suggestions, and identities the resolver marked `needs_review`.

**Why this tab is needed:**
Several parts of ContextEdge deliberately refuse to act on their own when confidence is low. Those refusals have to land somewhere, or they are just silence. This tab is where they land.

**Main functionality:**

- Lists pending semantic correlation suggestions and lets a reviewer accept or reject each one (`backend/src/contextedge/api/v1/correlations.py:172`).
- Lists fleet-group suggestions - devices or CIs that look like one managed group - with accept and reject (`correlations.py:70`).
- Lists identities in `needs_review` and lets a reviewer resolve them.

**Nothing here has been applied yet.** Accepting is what writes the correlation edge or resolves the identity. Rejections are durable, so a scheduled pass never re-raises the same pair.

**Who can see it:** `knowledge_manager`, `domain_admin` or `tenant_admin`.

**Example:**
A suggestion proposes linking a firewall change record to the Acme VPN incident because their chunk embeddings are close. A reviewer opens both, sees the change touched the same gateway, and accepts - and the episode cluster grows.

## 17. Graph Explorer

**What this tab is used for:**
Graph Explorer visually explores relationships between evidence, episodes, playbooks, decisions, sessions, users, entities, and actions.

**Why this tab is needed:**
Some operational questions are relationship questions. A table can show records, but a graph shows how records connect.

**Main functionality:**

- Shows graph statistics.
- Explores subgraphs by entity type, entity ID, depth, and domain.
- Shows neighbors around a selected node.
- Lets users click through connected nodes.
- Reviews agent-proposed dependency edges and approves or rejects them.
- Helps inspect context used by agents and reviewers.

**What keeps the graph honest:**

- **Edge types are a closed vocabulary.** 69 registered types; writing an unregistered one raises (`backend/src/contextedge/graph/edge_types.py:1`).
- **`weight` and `confidence` mean different things** - traversal importance versus belief - and both are written where both are meant (`backend/src/contextedge/graph/builder.py:63`).
- **Edges are temporal.** They carry `valid_from` / `valid_to`, so a point-in-time read is possible. The caveat is stated in the API response itself: historical edges combine with **current** node facts.
- **Relational rows become edges every 6 hours** through a materializer that is idempotent and additive-only (`backend/src/contextedge/graph/agent/materializer.py:54`).

**Agent proposals never become topology silently.** When an agent proposes a dependency it writes a `proposed_depends_on` edge at confidence 0.3, which is deliberately not traversable; approving it promotes it to `depends_on` and supersedes the proposal rather than deleting it (`backend/src/contextedge/api/v1/graph.py:142`).

**Example:**
An Acme auditor opens the VPN session graph and sees the evidence, the episode, the pattern, the playbook, the decision, the approval request, and the execution run connected in one view.

## 18. Contradictions

**What this tab is used for:**
Contradictions show conflicts between trusted playbooks and newer or competing evidence.

**Why this tab is needed:**
Operational knowledge can become stale. If a new ticket or knowledge-base article says the old fix is wrong, the system must surface that conflict before users continue relying on outdated guidance.

**Main functionality:**

- Lists detected contradictions.
- Shows conflict severity and status.
- Compares playbook knowledge against evidence.
- Allows users to mark contradictions as reviewed or resolved.

**How they are found:** a beat sweep runs every 12 hours (`backend/src/contextedge/workers/evaluation_tasks.py:88`). It is one of the few scheduled jobs that makes model calls, so it is a genuine cost line, and it keeps per-playbook scan state so it does not re-check what has not changed.

**Example:**
An approved Acme playbook says to restart the tunnel service first. A newer vendor bulletin in the knowledge base says restarting before renewing an expired certificate drops all sessions with no benefit. The contradiction is flagged for review.

## 19. Drift

**What this tab is used for:**
Drift shows playbooks that may be stale, expired, unvalidated, or receiving negative feedback.

**Why this tab is needed:**
Operational procedures change. A playbook that worked six months ago may become unsafe or ineffective. Drift keeps approved knowledge current.

**Main functionality:**

- Lists drift alerts.
- Shows signals such as validation age, expiry, and negative retrieval feedback.
- Helps users decide which playbooks need review.
- Offers a shortcut to regenerate a playbook from its pattern.
- Runs on a schedule through background workers - every 6 hours (`backend/src/contextedge/workers/evaluation_tasks.py:41`).

**What raises an alert** (`backend/src/contextedge/services/drift_service.py:13`) - read-only heuristics over approved playbooks: an `expiry_at` in the past; a `last_validated_at` older than **90 days**; three or more negative retrieval feedback events in the last 30 days; or the source pattern having grown after the playbook was generated.

**Do not confuse that with the ranking rule.** Runtime's freshness score is a separate number: a past expiry scores 0, otherwise it decays linearly over **180 days** since last validation, and a never-validated playbook sits at 0.5 (`backend/src/contextedge/search/hybrid_ranker.py:382`). So a drifting playbook is already being ranked lower before anyone opens this tab.

**Example:**
The Acme VPN certificate playbook has not been validated in 180 days and picked up three negative retrieval feedback events. Drift flags it on both counts, and Runtime has already been ranking it below fresher alternatives.

## 20. Evaluations

**What this tab is used for:**
Evaluations replay historical cases against the current retrieval and ranking logic.

**Why this tab is needed:**
Teams need to know whether ContextEdge is recommending the right playbooks. Evaluations test quality before changes affect real users.

**Main functionality:**

- Lists evaluation datasets.
- Creates datasets for test cases.
- Starts evaluation runs.
- Shows pass/fail and ranking results.
- Helps compare current retrieval behavior against expected answers.

**Why the result is trustworthy:** the harness calls the same `rank_playbooks` function the live Runtime tab calls (`backend/src/contextedge/services/evaluation_service.py:134`), so there is no separate "evaluation ranker" that can drift away from production.

**Example:**
An Acme knowledge manager builds a dataset of 20 past VPN and RADIUS incidents. After a weight change to the ranker, they run the evaluation to confirm the certificate-renewal playbook is still top for INC0010427-style symptoms.

## 21. Policies

**What this tab is used for:**
Policies define governance rules for retention, classification, access, retrieval, and approval gates.

**Why this tab is needed:**
Different organizations and domains have different compliance, access, and safety requirements. Policies let admins control how ContextEdge behaves.

**Main functionality:**

- Lists tenant policies grouped by type - retention, classification, access, approval.
- Creates new policies.
- Edits or deletes existing policies.
- Assigns a policy to a source, an evidence item, or a playbook.
- Supports governance for sources, evidence, retrieval, and execution.

**How policies bite, concretely:**

- **Access** policies marked `restricted` are excluded from search results for anyone who is not an admin (`backend/src/contextedge/search/access_control.py:12`).
- **Retention** policies set the archive window that the daily sweep applies.
- **Approval** policies are evaluated at two points in execution - when a run starts and when an approval is decided - and every evaluation, allow or deny, writes a `policy_checks` row.
- **Versioning tracks rules, not labels.** Editing a policy's `config` bumps its version; renaming or deactivating it does not (`backend/src/contextedge/api/v1/policies.py:133`).

**Example:**
An Acme tenant admin creates an approval policy requiring a second approver for any medium-risk production remediation, and bans self-approval. The certificate-renewal run is then gated by it, and the check is recorded either way.

## 22. Audit Log

**What this tab is used for:**
The Audit Log records important user and system actions.

**Why this tab is needed:**
For compliance and accountability, teams must know who changed what, who approved what, and who accessed sensitive information.

**Main functionality:**

- Lists audit events.
- Tracks admin, reviewer, retrieval, and policy actions.
- Supports filtering by event details.
- Provides an accountability trail for governance reviews.

**How capture works:** every mutating `/api/v1` request writes a row automatically after the response, with outcome `success`, `denied` or `failed` (`backend/src/contextedge/middleware/request_audit.py:25`). The insert runs off-thread on its own connection and swallows its own errors, because auditing must never break a request. Business-level actions such as pausing a sync also write their own explicit row.

**One thing to know:** unauthenticated 401 probes never resolve a tenant, so they exist only in the structured log line `http.mutating_request`, not in the table. Alert on that log line, not on the table, if you care about them.

**Example:**
During an Acme audit, the compliance team checks who approved the VPN certificate renewal and when the step was recorded as complete.

## 23. LLM Cost

**What this tab is used for:**
LLM Cost shows token usage, estimated spend, cache-hit rate, model/task breakdown, and tenant budget status.

**Why this tab is needed:**
AI calls cost money. Admins need visibility and budget controls so usage does not grow unexpectedly.

**Main functionality:**

- Shows estimated cost and token usage.
- Shows prompt, cached prompt, and completion token breakdown, plus the reasoning share.
- Shows model and task-level usage.
- Displays daily budget status.
- Allows tenant admins to configure token and cost caps.
- Supports warn or block behavior when a budget is exceeded.

**How the numbers are produced:** there is exactly one recorder. Every model and embedding call writes an `llm.usage` operational event, and both this dashboard and the budget gate sum the same events - there is no second counter to drift (`backend/src/contextedge/ai/observability.py:133`). Costs shown are estimates for dashboard use; the provider's bill is authoritative.

**Defaults matter in a demo:** a tenant with no budget row still gets the deployment defaults of 2,000,000 tokens/day, $25/day, action `block` (`backend/src/contextedge/config.py:191`). That default has frozen a live bulk backfill mid-run. Before a large first import, either raise the cap or set the action to `warn` for the window.

**A blocked tenant fails softly, not loudly.** Ingestion still lands rows; they just arrive without embeddings and without extracted identities. The signature is chunks stuck with a NULL embedding plus `llm.usage` events showing `outcome = budget_exceeded`.

**Example:**
After Acme enables episode reconstruction on a backlog, daily cost rises. The tenant admin sets a daily budget in warn mode first, watches for a week, then switches to block.

## 24. Pipeline Health

**What this tab is used for:**
Pipeline Health is the operator's view of the eight background queues and the end-to-end ingestion chain.

**Why this tab is needed:**
Every per-task metric can say "healthy" while the pipeline is dead. That is not hypothetical - it happened here: `correlate_evidence` starved behind 8,000 normalizations, and episodes, patterns and playbooks all stayed at zero while nothing looked wrong (`backend/src/contextedge/services/pipeline_health_service.py:1`).

**Main functionality:**

- Shows queue depth for each of the eight queues in pipeline order: extraction, correlation, embedding, hydration, pattern, evaluation, sync, default.
- Shows in-flight ("unacked") work, which is where all the work lives during a reconstruction wave while every queue reads zero.
- Shows a single SQL roll-up of the chain: evidence, embedded, identities, correlations, episodes, patterns, playbooks.
- Flags a backlog over 500 items.

**How to read it:** find the first zero in the chain. That is where the pipeline stopped. A depth of zero on `correlation` and `embedding` with a large `extraction` backlog usually means no worker is consuming those two lanes.

**Who can see it:** `tenant_admin`.

**Example:**
An Acme operator sees evidence climbing, embedded stuck at 15% of chunks, and the embedding queue at zero - the worker fleet was started without the `embedding` queue, so everything ingested is invisible to semantic search.

## 25. Settings

**What this tab is used for:**
Settings manages organization-level configuration such as tenant, workspaces, domains, and users.

**Why this tab is needed:**
ContextEdge is multi-tenant and role-aware. Settings gives admins one place to manage the structure of the organization inside the platform.

**Main functionality:**

- Shows tenant information (General tab).
- Lists and creates workspaces.
- Lists and creates domains.
- Lists users.
- Points at retention configuration (Retention tab).

**Be honest about the limits in a KT session:**

- The **Retention** tab is a pointer, not a console. It states that retention is managed through the policies API and backend defaults (`frontend/src/app/(dashboard)/settings/page.tsx:398`).
- Role bindings can be created through the API (`POST /api/v1/users/{id}/roles`) but there is no first-class screen for managing them here.
- **Role scope is stored but not enforced.** `RoleBinding` carries `scope_type` and `scope_id`, but the permission check is a plain name check (`backend/src/contextedge/deps.py:37`), so a domain admin bound to one domain effectively holds that role tenant-wide. Narrower scope exists only through service-token `allowed_domain_ids` on routes that consult it. Single-domain tenants are unaffected; multi-domain tenants must treat a role grant as tenant-wide.
- **Sidebar visibility is not security.** The frontend treats only `platform_super_admin` as a super-role while the backend also short-circuits `tenant_admin` and `admin`, so a tenant admin may not see a nav item the API would happily authorize.

**Example:**
An Acme tenant admin creates a "Network Operations" domain so VPN-related evidence, playbooks, and policies are managed separately from Finance Operations.

## End-to-End Example: The Acme VPN Incident Across Tabs

Scenario: Acme's corporate VPN starts dropping tunnels. ServiceNow raises INC0010427 on `vpn-gw-east-01`, a Teams thread runs alongside it, and an engineer emails a root-cause note quoting the ticket number.

1. **Sources** defines where ContextEdge collects from - ServiceNow, Teams, Gmail - and the Entity Inventory screen approves the ServiceNow `incident` table for sync.
2. **Sync Operations** confirms the incremental run pulled INC0010427 and its work notes.
3. **Evidence** stores the ticket, the surviving Teams messages, and the email. Coordination-only chatter ("any update on the VPN?") never became a row.
4. **Identities** resolves `vpn-gw-east-01` to one canonical device from three spellings.
5. **Correlations** links the ticket, the thread and the email into one canonical case - the email through its quoted ticket number, at confidence 1.0.
6. **Episodes** reconstructs the timeline: complaint, diagnostics, failed restart, certificate check, renewal, confirmation.
7. **Review Queues** holds a low-confidence suggestion that a firewall change record belongs to the same case; a reviewer accepts it.
8. **Patterns** groups this episode with two earlier certificate-expiry incidents.
9. **Negative Knowledge** records "do not fail over before checking certificate expiry".
10. **Playbooks** generates "Renew VPN gateway certificate and restart the tunnel service", with the SOP's backup step cited as `[kb-1]`.
11. **Runtime** recommends that playbook the next time similar symptoms arrive.
12. **Sessions** stores the live case file.
13. **Decisions** records why renewal was chosen over failover.
14. **Review Queue** gets the human approval for the side-effecting step.
15. **Execution** records the approved step, its tool invocation, and the verification verdict 30 minutes later.
16. **Graph Explorer** shows evidence, episode, pattern, playbook, decision, approval and run connected.
17. **Contradictions** flags the vendor bulletin that disagrees with the old restart-first guidance.
18. **Drift** later flags the playbook once it passes 90 days without validation.
19. **Evaluations** confirms the same symptoms still retrieve the right playbook after a ranker change.
20. **LLM Cost** shows what the reconstruction and generation actually cost.
21. **Pipeline Health** is where you check first if any of the above produced nothing.
22. **Policies** and **Settings** control who can do each action and under which rules.
23. **Audit Log** records who approved the renewal and when.
