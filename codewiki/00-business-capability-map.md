# Business capability map

## Summary

After reading this page, a business or product stakeholder should know what ContextEdge does end to end, which user groups own which parts of the workflow, and where to go next in this wiki for deeper detail.

## Business picture

Every organization already generates the knowledge it needs to solve recurring problems—it just lives scattered across tickets, chat threads, emails, and wikis. ContextEdge turns that scattered activity into a **managed operational memory** that grows more valuable over time.

The core value chain is:

1. **Capture** — Automatically collect evidence from the systems teams already use, with no manual data entry.
2. **Organize** — Resolve duplicates, link related people and assets, and build a single source of truth per customer.
3. **Learn** — Surface what happened, what keeps happening, and what should never be repeated — while keeping that separate from what the documentation merely *claims* works.
4. **Guide** — Publish reviewed, approved playbooks so the right answer appears the next time a similar problem strikes.
5. **Trust** — Maintain full traceability, human review gates, and retention controls so every recommendation can be explained and audited.

The result: faster incident resolution, fewer repeat mistakes, and a clear audit trail that satisfies compliance requirements—all without asking teams to change how they work today.

| Reader | Main question | Best next page |
| --- | --- | --- |
| Tenant admin | How is my organization set up and governed? | [14-control-plane-tenants-roles-policies.md](./14-control-plane-tenants-roles-policies.md) |
| Domain admin | How do I onboard sources and control operational scope? | [03-ingestion-connectors-and-sync.md](./03-ingestion-connectors-and-sync.md) and [15-dashboard-and-operator-workflows.md](./15-dashboard-and-operator-workflows.md) |
| Knowledge manager | How do incidents become trusted playbooks? | [07-episodes-patterns-playbooks.md](./07-episodes-patterns-playbooks.md) |
| Analyst or responder | How do I search evidence and explain recommendations? | [05-search-hybrid-and-access.md](./05-search-hybrid-and-access.md) and [10-governance-sessions-execution-audit.md](./10-governance-sessions-execution-audit.md) |
| Compliance or operations lead | How do we prove control, retention, and review? | [10-governance-sessions-execution-audit.md](./10-governance-sessions-execution-audit.md), [11-retention-and-operational-events.md](./11-retention-and-operational-events.md), and [14-control-plane-tenants-roles-policies.md](./14-control-plane-tenants-roles-policies.md) |

## Technical walkthrough

1. **Organize the customer boundary** - Every workflow starts with a tenant, then optional workspaces and domains, then users and roles. This is how the product knows which evidence belongs to whom and who may govern it. In code: `backend/src/contextedge/models/tenant.py`, `backend/src/contextedge/api/v1/tenants.py`, `backend/src/contextedge/api/v1/workspaces.py`, `backend/src/contextedge/api/v1/domains.py`, `backend/src/contextedge/api/v1/users.py`.

2. **Connect operational systems** - Sources represent mailboxes, chat systems, ticket systems, or local evidence bundles. Admins can discover source objects, approve them for sync or backfill, and track sync runs. In code: `backend/src/contextedge/api/v1/sources.py`, `backend/src/contextedge/api/v1/sync.py`, `frontend/src/app/(dashboard)/sources/`.

3. **Normalize evidence into a durable record** - Raw payloads become normalized evidence items with provenance, summaries, access policy hooks, attachments, and thread references. Payloads over 32 KB are stored in object storage (MinIO) with only a stub in the database, and every body that clears the relevance gate is also split into retrieval-sized chunks with their own embeddings — a short ticket makes one chunk, a long thread makes many — so search can find a fact buried deep inside a long record. This is the foundation for search, extraction, and governance. In code: `backend/src/contextedge/services/ingestion_persistence.py:19` (`persist_ingestion_events`, offload threshold at line 16), `backend/src/contextedge/services/evidence_normalization.py`, `backend/src/contextedge/services/evidence_chunk_service.py:43` (`write_chunks`), `backend/src/contextedge/models/evidence.py`.

4. **Convert evidence into reusable memory** - The system derives episodes, patterns, identities, correlations, contradictions, negative knowledge, and **operational decisions** so teams can move from "what happened" to "what keeps happening," "who decided what," and "what should not be done again." Decision extraction identifies actions taken by people (e.g., "engineer restarted the server," "manager approved access") directly from evidence text and links actors and targets to canonical identities in the context graph. Two newer capabilities close the loop on recurrence. First, an hourly **AI review sweep** can either stamp a verdict on draft episodes for the human queue (advisory mode) or approve the ones that clear deterministic quality floors (auto-approve mode); the sweep ships **off** and someone has to turn it on. Second, every approved episode is distilled into an **issue signature** — a generalized problem fingerprint — so a repeat of the same failure months later is linked back to its precedent instead of being re-learned from scratch. Pattern clustering then groups approved episodes, and each new or grown pattern that clears a 0.5 confidence floor drafts a playbook candidate. In code: `backend/src/contextedge/services/episode_service.py:114` (`create_episodes_from_evidence`), `backend/src/contextedge/services/episode_review_service.py:174` (`ai_review_episode`), `backend/src/contextedge/services/issue_signature_service.py:89` (`extract_issue_signature`), `backend/src/contextedge/services/pattern_service.py`, `backend/src/contextedge/services/identity_service.py:810` (`link_evidence_identities`), `backend/src/contextedge/services/decision_service.py:21` (`link_evidence_decisions`), `backend/src/contextedge/services/correlation_service.py:197` (`correlate_evidence_item`), `backend/src/contextedge/services/contradiction_service.py`, `backend/src/contextedge/api/v1/negative_knowledge.py`.

5. **Keep "what happened" apart from "what the manual says"** - This is the newest and, commercially, one of the most important distinctions the product makes. An **episode** is an account of something that actually occurred. A **knowledge case** is a reconstruction of what a curated source — a KB article, an SOP, vendor documentation — *claims* resolves a problem. Both are valuable, and they are not the same kind of fact.

   Until recently the system could not tell them apart, and the consequences ran straight into the parts customers trust most: a KB article reconstructed as an episode asserts that an engineer did something and it worked, so playbook generation treated it as proof a step works, pattern counts treated it as a recurrence, and the agent cited it as a real case. When the defect was diagnosed, **299 episodes turned out to have been built purely from documentation**, 8 of them from before the knowledge import that made the problem visible — so this was a long-standing quiet defect, not a new regression. (That 299 is the diagnosis-time count; the clean-up migration described below moved 482 rows when it ran. The two are separate measurements taken at different moments, and nothing in the repo reconciles them.)

   Three things now keep the two apart. First, episode creation **requires at least one observational source**: a cluster made only of knowledge no longer becomes an episode (`backend/src/contextedge/workers/extraction_tasks.py:1014,1219`). Knowledge still participates in everything else — it is still searched, still correlated, still in the graph, and still able to seed a pattern; only the "this happened" claim is withheld. Second, knowledge cases live in **their own tables** rather than as a flag on episodes, so a query that forgets the distinction fails loudly instead of returning a quietly wrong number (`backend/src/contextedge/models/knowledge_case.py:46,139`). Third, a pattern now carries an **evidence ledger** recording what each piece of support is worth, so a reviewer sees "1 article documents this, 8 incidents observed it, 7 of them worked" instead of a flat count of 9 (`backend/src/contextedge/models/pattern.py:87`; `backend/src/contextedge/services/knowledge_case_service.py:246`).

   This also unlocks a genuine **cold start**: a documented failure mode nobody has hit yet can seed a pattern and be found in retrieval before the first incident, while staying below the confidence floor that authorises a playbook — so the system never writes a procedure from a claim no incident has confirmed (`backend/src/contextedge/services/knowledge_case_service.py:49,55`). **Status:** the tables, the rules, and the attach-or-seed logic are built and a one-off migration moved 482 old episodes into 135 knowledge cases, but **nothing on the ingest path creates a knowledge case yet** — a KB article arriving today still does not become one. Details in [01](./01-end-to-end-pipeline.md) and [07](./07-episodes-patterns-playbooks.md).

6. **Publish governed guidance** - Playbooks are versioned, reviewed, approved, and then exposed to runtime retrieval only when they meet the product's governance bar. In code: `backend/src/contextedge/services/playbook_service.py`, `backend/src/contextedge/models/playbook.py`, `backend/src/contextedge/api/v1/playbooks.py`.

7. **Support live resolution and controlled execution** - Responders can search for evidence, open sessions, run runtime match, inspect explain payloads, and process human approval requests before higher-risk automation proceeds. Governed execution steps are written into the graph as they happen — `executes` when a run starts, `requires_approval` when it pauses for a human, `approved_by` / `denied_by` / `modified_by` when someone decides, `execution_outcome` when it ends — so every automated or human decision is traversable and auditable alongside evidence and identity links. In code: `backend/src/contextedge/api/v1/runtime.py:130` (the match route calls `rank_playbooks` directly), `backend/src/contextedge/services/session_service.py:38,139`, `backend/src/contextedge/services/execution_service.py:902,1287,1430,1692` (the edge writes above), `frontend/src/app/(dashboard)/runtime/page.tsx`, `frontend/src/app/(dashboard)/sessions/page.tsx`, `frontend/src/app/(dashboard)/execution/page.tsx`.

8. **Keep the memory healthy over time** - Evaluations, drift signals, notifications, audit logs, and retention policies show whether the memory is still trustworthy, still current, and still compliant. Hourly dedup sweeps retire duplicate drafts, and AI calls are metered against a per-tenant daily token and cost budget so one noisy tenant cannot exhaust the deployment (a tenant with no budget row of its own falls back to the deployment defaults: 2,000,000 tokens and $25 a day, action "block"). One exception worth knowing: the parent-evidence embedding written during normalization passes no tenant context, so it is neither budget-gated nor billed to a tenant (`backend/src/contextedge/workers/extraction_tasks.py:68`); chunk embeddings are both. In code: `backend/src/contextedge/services/evaluation_service.py:26` (`execute_evaluation_run`), `backend/src/contextedge/services/drift_service.py:104` (`check_playbook_drift`), `backend/src/contextedge/services/tenant_budget_service.py:234` (`check_budget`), `backend/src/contextedge/services/notification_service.py`, `backend/src/contextedge/services/retention_service.py`.

### On the way: knowing what is happening right now

Everything above is **retrospective** — it answers "what happened, what keeps happening, what should we do about it." The one thing the product cannot yet answer is "what is happening **right now**, and what changed just before it started."

The **data model** for that has landed — the tables and the rules, and nothing else. On paper, an **operational situation** is meant to group many live signals into one occurrence, record which business services and infrastructure it appears to affect — including, deliberately, what appears to be **healthy**, since ruling things out narrows a root cause as much as ruling them in — and rank the recent changes that might explain it (`backend/src/contextedge/models/situation.py`). Two rules are already enforced by the database rather than by convention: a change that happened *after* the symptoms started can never be recorded as a suspected cause (it can still be recorded as the remediation or the rollback), and a situation merged into another must say where it went, so "why did these two become one incident" always has an answer.

**Nothing does any of that yet, and no code for it exists.** This is not a feature waiting on data — the grouping, the impact assessment, and the change ranking are all unwritten. A search of the whole backend finds `OperationalSituation` in four places: the model file, the model index, migration `0074`, and one test that checks the constraints. No service, worker, or API route touches it, and the tables are empty.

Even if that logic existed, this deployment could not fill it: only Zoho Desk is connected, so there are **no change records, no monitoring alerts, and no configuration-item or service inventory** to correlate against. Reaching this capability needs connectors for change management, monitoring, and topology *and* the correlation code, in that order. Treat it as a committed direction, not a shipped feature — and if a demo or a deck implies ContextEdge is doing change correlation today, it is wrong.

## Example: Acme VPN data at this stage

This section shows the kind of information that flows through each capability layer for one incident.

**Starting point** — a VPN outage generates activity across three systems:

| System | What happens |
| --- | --- |
| Jira | Ticket JIRA-4521: "VPN connection drops after Windows update KB5032190" filed by jsmith@acme.com |
| Teams | 12-message thread in #vpn-support: engineers discuss AUTH_CERT_EXPIRED errors on vpn-gw-east-01 |
| Email | Engineer sends root-cause note: "Gateway certificate invalidated by new patch chain" |

**What ContextEdge produces from this activity:**

| Capability | Output |
| --- | --- |
| Evidence normalization | Each record becomes its own evidence item (a re-filed duplicate of the ticket dedupes by content hash); a noise gate drops the "any update?" chatter from the hydrated Teams thread before any AI call |
| Correlation into one case | The Jira ticket, the Teams thread, and the email quoting the ticket are linked into one canonical case — correlation connects distinct records; dedupe only collapses identical ones |
| Identity resolution | "jsmith," "John Smith," and "J. Smith (IT)" resolve to one canonical person, and `vpn-gw-east-01` to one canonical device. "KB5032190" deliberately does **not** become an identity — patch, version, environment, and vendor are facet types, so they are stored on the evidence itself (`source_facets` / `applicability`) where they can be filtered on instead of adjudicated against every other version string; see [12](./12-identity-resolution-and-thread-hydration.md) |
| Decision capture | "Engineer restarted vpn-gw-east-01" and "Manager approved certificate renewal" are extracted as decision graph edges linking actors to targets |
| Episode reconstruction | One structured episode: "Corporate VPN auth failure after KB5032190" with 5 ordered steps from complaint through remediation. It qualifies because the cluster holds a real ticket and real thread messages — something observed |
| Knowledge case (not an episode) | Acme's KB article "Rotating the VPN gateway certificate" is reconstructed too, but as a **knowledge case**: documented cause, documented resolution, and steps with an *expected* outcome. It has no outcome, no duration, and no date it occurred, because it never occurred. On its own it can no longer be narrated into an episode claiming the fix worked |
| Episode review | With the sweep turned on, the hourly AI review stamps an advisory verdict — or approves the draft outright, if it clears the quality floors. A human approval writes the same fields plus a named reviewer |
| Issue signature and recurrence | The approved episode mints signature "remote_access \| tls_certificate \| certificate_expired"; the same failure next year links back to this case as a precedent |
| Pattern detection | Linked to existing pattern: "Certificate expiry after Windows cumulative updates" (7 prior episodes). The KB article attaches to the same pattern as *documented* support, so the pattern reads "7 observed, 1 documented" rather than an undifferentiated 8 |
| Playbook governance | Candidate playbook "VPN Certificate Rotation After Patch Tuesday" enters review queue |
| Runtime retrieval | When the next similar outage occurs, the approved playbook ranks first with 92% confidence |

## Design decisions

- **One product spans intake, memory, and delivery** - Why: operators do not want one tool for ingestion, another for knowledge curation, and a third for runtime retrieval. Tradeoff: the codebase is broad, so documentation has to help readers navigate across many modules.

- **Derived knowledge stays tied to evidence** - Why: business users need to trust where summaries, patterns, and playbooks came from. Tradeoff: more linking tables, more metadata, and more governance steps than a simple note-taking system.

- **Human review remains central, with AI assistance opt-in** - Why: approved playbooks, execution approvals, audit logs, and policy attachments keep operational memory safe to use in production. An AI reviewer can triage or (behind deterministic quality floors) approve draft episodes, but it is off by default, its verdicts are stamped and auditable, and a machine approval is permanently distinguishable from a human one (`reviewer_user_id` stays empty). Tradeoff: the fastest path is not always the path the system allows, and enabling auto-approve is a deliberate configuration decision.

- **The same product serves people and service accounts** - Why: responders, managers, and integrations all need retrieval. Tradeoff: runtime behavior has to reconcile role-based, domain-based, and risk-based constraints.

- **A document's claim is never counted as an observed outcome** - Why: the product's core promise is that a recommendation can be traced to evidence. If a KB article can be reconstructed into an episode, the system starts citing documentation as proof that a fix worked in practice — which is the one failure that would quietly undermine every number above it: pattern strength, playbook grounding, and the agent's citations. Episodes now require an observational source, knowledge cases get their own tables, and the database itself refuses to record a documented claim as an empirical outcome. Tradeoff: two shapes of "reconstructed case" to maintain and join across, a real migration rather than a flag, and an honest reduction in headline episode counts — 482 rows moved out of `episodes` on this deployment.

- **A documented-only pattern is allowed to exist, but not to write procedure** - Why: the moment documentation is most valuable is *before* anyone hits the problem, so a written-down failure mode should be findable immediately. But nothing should turn an unverified claim into an approved procedure. Seeding such a pattern below the playbook-generation floor gives retrieval the benefit without granting the authority. Tradeoff: it inflates the pattern count — on this deployment roughly **55% of migrated knowledge cases (75 of 135) seeded patterns carrying no empirical support at all** — so any surface showing pattern counts needs to show the support state alongside, or reviewers will over-read them.

- **Failure is treated as knowledge, not just noise** - Why: negative knowledge, contradictions, drift alerts, and evaluation feedback prevent the memory from becoming a one-way archive of only successful actions. Tradeoff: teams must invest in curation, not just ingestion.

## Code map

| Concern | Module path | Key symbols | When it runs |
| --- | --- | --- | --- |
| Business entry points | `frontend/src/components/shell/sidebar-nav.tsx` | `navItems` | Every dashboard render |
| API surface | `backend/src/contextedge/api/v1/__init__.py` | `router`, `include_router` | App startup |
| Organization model | `backend/src/contextedge/models/tenant.py` | `Tenant`, `Workspace`, `Domain`, `User`, `RoleBinding` | Data modeling |
| Source onboarding | `backend/src/contextedge/api/v1/sources.py:81,238,461` | `create_source`, `approve_source_object`, `local_ingest` | Admin and ingestion setup |
| Evidence persistence | `backend/src/contextedge/services/ingestion_persistence.py:19` | `persist_ingestion_events` | After sync batches arrive |
| Episode synthesis | `backend/src/contextedge/services/episode_service.py:114` | `create_episodes_from_evidence` | Correlation worker, debounced |
| Observational-source gate | `backend/src/contextedge/workers/extraction_tasks.py:1014,1219` | `_cluster_has_observational_evidence` | Gate 6 of 7 in `_reconstruct`, before the LLM call |
| Knowledge cases | `backend/src/contextedge/models/knowledge_case.py:46,139` | `KnowledgeCase`, `KnowledgeCaseStep` | Migration `0072`; populated by `0073` |
| Pattern evidence ledger | `backend/src/contextedge/models/pattern.py:87`; `backend/src/contextedge/services/knowledge_case_service.py:246` | `PatternEvidence`, `pattern_support` | On attach/seed; read by reviewers |
| Knowledge attach-or-seed | `backend/src/contextedge/services/knowledge_case_service.py:144` | `attach_case` | Backfill only — no ingest caller yet |
| Situation model | `backend/src/contextedge/models/situation.py:118`; `backend/src/contextedge/graph/edge_types.py:141` | `OperationalSituation`, `_SITUATION` | Migration `0074`; **schema only — no correlation logic exists** |
| Episode AI review | `backend/src/contextedge/services/episode_review_service.py:174` | `ai_review_episode`, auto-approve floors (lines 42-44) | Hourly sweep (`evaluation` queue) |
| Issue signatures / recurrence | `backend/src/contextedge/services/issue_signature_service.py:76,89` | `signature_key_for`, `extract_issue_signature` | On every episode approval |
| Playbook governance | `backend/src/contextedge/services/playbook_service.py:217,360` | `transition_playbook`, `create_playbook_version` | Knowledge curation |
| Runtime retrieval | `backend/src/contextedge/api/v1/runtime.py:130`; `backend/src/contextedge/search/hybrid_ranker.py:213` | match route, `rank_playbooks` | Live resolution |
| Session trace | `backend/src/contextedge/services/session_service.py:38,139` | `create_resolution_session`, `append_trace_event` | Incident investigation |
| Decision capture | `backend/src/contextedge/services/decision_service.py:21` | `link_evidence_decisions` | Normalization worker |
| LLM spend gate | `backend/src/contextedge/services/tenant_budget_service.py:234` | `check_budget` | Before each AI call that carries tenant context |
| Quality loop | `backend/src/contextedge/services/evaluation_service.py:26` | `execute_evaluation_run` | Offline evaluation |
| Drift monitoring | `backend/src/contextedge/services/drift_service.py:13,104` | `list_drift_alerts`, `check_playbook_drift` | Ongoing health checks |

## Acme VPN incident (this layer)

For Acme Corp, the VPN outage is not just one ticket. It becomes a tenant-scoped operational story: source onboarding determines which systems feed the memory, evidence normalization captures the facts, correlation ties the ticket, the Teams thread, and the email into one case, knowledge managers (assisted by the AI review sweep) convert the facts into episodes and playbooks, the issue signature makes next year's repeat of the same certificate expiry land next to this year's fix, responders use runtime match during the next outage, and audit plus retention rules explain what the company knew, when it knew it, and why a given playbook was shown.

Acme's KB article on rotating the gateway certificate rides alongside that story without being folded into it. It is searched, correlated, and used to ground the playbook — but it is held as a knowledge case, so the pattern behind the playbook can say plainly that seven engineers resolved this and one article describes how, rather than implying eight resolutions. What Acme still cannot ask is "which change caused this outage": that needs the situation layer, and behind it a change-management feed the deployment does not have.

## What this deployment actually holds

The capabilities above describe the product. It is worth being explicit about the gap between that and the live data, because several capabilities are correctly built and simply have nothing to act on:

- **One connector is live: Zoho Desk.** No email, chat, Jira, ServiceNow, monitoring, or CMDB source is connected.
- **Three evidence types are present**: `thread_message`, `ticket`, and `kb_article`. The Acme examples in this wiki span Jira, Teams, and email because they illustrate the intended shape, not this deployment's contents.
- **All 849 entities are `topic` or `knowledge_category`.** There are no person, device, service, or configuration-item entities, and **no topology edges** — so anything described as reasoning over infrastructure relationships has no graph to walk here. (Same count recorded in [KNOWN_GAPS.md](./KNOWN_GAPS.md) and [INCIDENT_DIAGNOSIS_ROADMAP.md](./INCIDENT_DIAGNOSIS_ROADMAP.md) under C1.)
- **Zero change records and zero monitoring alerts.** The situation tables are empty for two independent reasons — there is no correlation code to write them, and there would be nothing to correlate if there were — so change-correlation cannot be demonstrated here at all.
- **Knowledge cases exist only from the one-off migration.** New articles do not become cases on ingest.

None of this is broken; it is a deployment connected to one ticketing system. But a reader evaluating the product should not read "supports change correlation" as "is doing change correlation here."

## Further reading

- [15-dashboard-and-operator-workflows.md](./15-dashboard-and-operator-workflows.md) - what business users actually see in the product
- [01-end-to-end-pipeline.md](./01-end-to-end-pipeline.md) - the same product story from a system pipeline angle
- [14-control-plane-tenants-roles-policies.md](./14-control-plane-tenants-roles-policies.md) - org setup, roles, and policies
- [`STANDALONE_OPERATIONAL_MEMORY_PRD.md`](../STANDALONE_OPERATIONAL_MEMORY_PRD.md) - product-level scope and intent
