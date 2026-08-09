# Known gaps and caveats

Short list of implementation gaps and operational caveats called out in the codewiki and root documentation. Use this when the product surface looks more complete in the architecture than it does in the current UI or environment.

## 2026-08-05 external end-to-end review — validated, partially fixed

An external review assessed the repo as "advanced prototype, controlled-pilot ready, not enterprise-production ready" with five P0/P1 families. Each concrete claim was validated against the code before acting. Fixed in the same patch set (all with tests; suite at 1,394):

- **Evidence deletion guards (review P0-2, confirmed exactly as reported).** Bulk-delete deleted correlation edges and attachments for *caller-supplied* UUIDs before any tenant check — cross-tenant dependency deletion was live. All destructive evidence routes now resolve-and-authorize first (any foreign id fails the whole request with 404, before any delete statement), refuse legal-hold items with 409, and purge preserves held evidence plus its raw objects (audited count). Uses the shared `exclude_legal_hold()` fragment per its own module contract. Background retention already honoured holds; the gap was the direct APIs only.
- **Batch embedding budget bypass (review P1-8, confirmed).** `generate_embeddings_batch` took no tenant context: ingestion embeddings — the bulk of embedding spend — bypassed a blocked tenant's cap and recorded as `tenant_id=unknown`. Now takes `tenant_id`/`db`, enforces the same gate as the single-text path (re-checked per sub-batch so a long ingest stops at the cap rather than finishing past it), attributes usage, and the chunk worker passes its context. Tests pin both the block and legacy-caller compatibility.
- **Normalization scoping (review P0-3, confirmed).** See the resolved `workspace_id`/`domain_id` entry below.
- **Documentation drift (confirmed).** README claimed Alembic head `0031` and a `0001..0015` chain against an actual head of `0053`; RUNBOOK named `0013`. All now say "trust `alembic heads`, not a number in a doc."

Validated but **deliberately not fixed here** — each is architecture, not a patch:

- **Scoped RBAC (P0-1):** see the extended role-bindings entry below.
- **Outcome loop is schema-only (P0-4, confirmed):** `CaseOutcome` / `CaseStateTransition` have model definitions and no writer anywhere — grep finds only the class declarations. Until a case-lifecycle service exists, MTTR / first-time-right / deflection claims are unmeasurable. Narrative docs describing these writes as current behaviour should be read as design intent.
- **MAF governed-playbook contract (P1-5):** the plugin exposes graph/CMDB/risk tools but not playbook matching, explanation, full published retrieval, feedback, or outcome capture — the product's highest-value workflow is not agent-callable. Needs its own design pass (five-tool contract sketched in the review).
- **Graph API scope consistency (P1-6):** `/graph/agent-subsets` builds a full scoped projection, but CMDB topology / change-risk / fix-applicability routes pass only `tenant_id` — a domain-limited agent identity can read wider than its projection would allow.
- **Evaluation as a release gate (P1-7), SSO/service-token hardening + metrics exposure (P1-10), CI depth (frontend lint currently failing, no migration replay, no real-datastore integration lane):** all confirmed; roadmap items.

A companion **graph-schema review** (same date, verdict: "storage foundation adequate, MAF contract incomplete — 3/5, no engine change needed") was validated the same way. Fixed immediately, each with tests:

- **Ontology drift (confirmed, and then some):** Zoho wrote `customer_account`, `knowledge_category`, `topic` unregistered in `ENTITY_TYPES`; all three registered, and `test_entity_type_registry.py` now scans every reference-service source for written entity types and fails on any the registry doesn't know. The test's own first run produced a false positive (`os_name`, a CI trait key matching the tuple regex) — fixed in the test, documented inline.
- **Connector relationships invisible to maf.v1 (confirmed):** `affects_ci` and `assigned_to_group` — written by every ticket connector — are now allowlisted, so an agent holding a CI seed can discover its incidents and owning team. `mentions_identity` stays excluded deliberately: measured fan-out of 40–70 edges per handful of tickets would spend the budget on identity hubs instead of topology.
- **Execution hydration omitted verification (confirmed):** `verification_status` / `verified_at` now project — "completed" and "completed, then verified stable" are different precedents and were collapsed. `verification_details` (unbounded JSONB) stays out.
- **weight-as-confidence conflation (confirmed, including in code written days earlier in this very repo):** `ensure_edge` now accepts `confidence`; `persist_knowledge_links` passes similarity as both weight (traversal importance — a better match matters more) and confidence (belief).

Still open from the graph review, all architectural: a versioned node/edge/triple ontology registry shared by writers, materializer, profiles and hydrators; `playbook_version` as a projection node (version-qualified evidence trace); execution steps / tool invocations / case transitions in the graph; event-driven materialization with true reconciliation (today's is additive-only, 6h cadence, `replace_edge` has no production callers); relationship provenance in MAF responses (edge id, origin, derivation, validity interval); DB-enforced tenant/domain endpoint consistency plus integrity audits; coherent `as_of` semantics (historical edges currently combine with current node facts — the selector says so, callers must not draw historical operational conclusions); and task-specific projection profiles (case / topology / knowledge / governance) instead of one widening allowlist.

The July 2026 production-readiness review's P0/P1 code gaps were closed in one branch. Headlines (each with tests):

- **Layered identity resolution (migration `0033`).** Strong identifiers (email/username/hostname/fqdn/ip/serial/external id) resolve deterministically at 1.0; typed exact alias matching is entity-type-scoped; LLM candidate adjudication may abstain (`needs_review`), auto-links only above per-type thresholds (person 0.95); unmatched mentions create `provisional` identities instead of trusted 0.8 ones. Strong aliases are unique per tenant. Merges mark the survivor `verified` and enqueue `extraction.rebuild_identity_snapshots` to repair the cached JSONB refs.
- **Real ANN indexing (migration `0032`).** halfvec expression HNSW on all four embedding columns — see the corrected HNSW entry below.
- **Correlation gating.** Identity co-occurrence requires resolved/verified identities + 7-day window + non-person entity (0.65–0.75) or ≥2 shared identities (0.5); person-only single-identity correlation is dropped entirely. `CaseLink.evidence_id` no longer clobbered by the newest evidence.
- **Execution governance.** Unknown safety classes fail closed; outcome enum validated; completion refuses while steps are open; abort/complete restricted to initiator/domain-admin; approvals verified against the run in the URL; playbook approval policies (max automation mode, min-safety-class approval, approver roles, self-approval ban) are now *evaluated* at start and decide time, not just stored.
- **Security/ops basics.** Fernet key required outside development (no more per-call transient keys); login checks `status=active` and survives duplicate emails across tenants; `/ready` actually probes DB + migration head + Redis; destructive seed scripts refuse to run outside development.
- **Retention/audit/notifications.** Archive daily + purge weekly on Beat (`settings.retention_purge_mode`, default `soft_purge`); `apply_legal_hold` tenant-scoped; soft-purge scrubs `evidence_chunks`; audit middleware records denied/failed mutations; email/webhook notifications deliver when configured (explicit `skipped_unconfigured` otherwise).
- **ServiceNow.** Compound `(sys_updated_on, sys_id)` checkpoint (no boundary-second loss), paged incremental sync, retry/backoff with Retry-After.
- **Graph/MAF hardening.** `ensure_edge` is ON CONFLICT-safe; one canonical domain-derivation rule across all edge writers; `GraphRelationshipMaterializer` on Beat (6h); traversal capped per frontier node; MAF provider truncates long conversations instead of dropping context and fences injected graph data as untrusted; generated playbooks carry `evidence_refs` and a policy-derived risk tier.

## Resolved: ticket-number bridging as case membership (2026-08-01, migration `0038`, P1)

The correlation review's P1, with its central correction honored: a quoted ticket number proves "this evidence relates to that case" — never "every ticket quoted here is one case". `case_identifiers` registers each ticket source's human-readable number (ServiceNow `number`, Jira `key`, SapphireIMS `ticket_id`) against its canonical case at correlate time; conversational sources (teams/gmail/local_file) extract ticket-shaped tokens from title+body and **resolve-then-link** into `evidence_case_memberships` (subject 0.98 / body 0.9), storing unknown tokens as `pending_identifier_mentions` reconciled the moment the ticket registers — ingestion-order independent. Guards: **multi-ticket digest** (≥3 distinct cases in one message → `mentioned_only` at 0.5, which the episode cluster resolver never expands through), **cross-system ambiguity abstention** (same value registered by two systems → no membership, logged), identifier-case mismatches logged never clobbered, membership inserts idempotent and race-safe. The episode cluster resolver expands through active non-mentioned-only memberships with `ticket_ref:*` reasons — the Acme email quoting INC0010427 now lands in the incident's cluster deterministically. Not yet: ticket-body text mentions on ticket sources themselves (structured reference fields already cover snow/jira), transcript-specific ASR confidence.

## Resolved: conversational foundations — reconstruction debounce + Teams metadata (2026-08-01)

Two prerequisites from the conversational-resolution design. (1) **Debounced, starvation-guarded reconstruction**: correlate now dispatches reconstruction with a 180s countdown, and `_reconstruct` re-checks settlement at run time — if the cluster received evidence within the window, the task defers on SQL alone (no LLM spend) and the later-scheduled task from the newer evidence proceeds; a never-quiet channel still gets its first synthesis within 30 minutes of the cluster's oldest evidence (starvation guard), with supersede-on-growth catching up once quiet. Manual reviewer triggers bypass via `settle=False`. Superseded drafts now emit `episode.draft_superseded` lineage events naming both fingerprints. (2) **Teams conversational metadata**: the connector previously discarded everything the resolver layers depend on; message events now carry `message_id`, `reply_to_id` (the reply-inheritance anchor), `is_bot`/`from_application` (bot cards are structured payloads, not human assertions), `last_edited_at`/`is_deleted`/`deleted_at` (edits can invert meaning; deletions must mark evidence withdrawn, not vanish), slimmed attachments and mentions; hydrated replies carry reply/bot flags. **Still open from the conversational design**: reply-inheritance correlation tier with explicit-dissociation veto (next), thread-topic state, person-role-to-case indexes, entity→active-case candidates, provisional pre-ticket cases, message-span-level multi-case references, negative-signal scoring, quoted/forwarded content separation, ASR-confidence handling for transcripts.

## Resolved: episode cluster materialization + provenance (2026-08-01, migration `0037`, P0)

The correlation/episode review's P0, verified claim by claim before building: (1) reconstruction received only the newly-correlated evidence id ("comma-separated ... MVP wiring") — correlation edges were created and then ignored, so multi-source incidents still produced single-source episodes; (2) every item reached the extractor as `source_type: "evidence"`; (3) every extracted episode was stamped with the FULL input evidence list, so LLM splits smeared membership. Now: `services/episode_cluster_service.resolve_episode_cluster` materializes the connected component (CaseLink canonical cases + CorrelationEdge, both directions) before reconstruction — visibility-fenced in SQL (tenant, legal hold, pending redaction), **time-fenced** (a member must be within 30 days of its nearest seed — correlation chains can't drag in last quarter's ticket; undated evidence fails open with the 50-member cap as backstop), hop/size bounded with truncation recorded. Items carry real `source_type` + `source_role` (ticket / working_discussion / external_communication …) and sort by time. Episode prompt **v2** (v1 immutable per the eval-baseline convention; also fixes this family's doubled-brace bug) labels evidence `[ev-N]` and requires per-episode + per-step `evidence_refs`; the extractor translates labels back to real ids and drops minted ones. Persistence assigns per-episode membership from validated citations (full-cluster fallback is logged, never silent), writes normalized `episode_evidence_links` rows carrying the cluster reason, and stamps `episodes.cluster_fingerprint` — powering draft idempotency (same cluster → no duplicate draft) and supersede-on-growth (subset-cluster pending drafts → `reviewer_state=superseded`, invisible to the agent surface). Reviewer actions: add/remove evidence endpoints updating both JSONB and links. Still open from the review: split/merge review workflows, P1 ticket-number membership bridging, P2 entity rarity, P3 semantic suggestions, P4 field-level authority.

## Resolved: Zoho Desk connector (2026-08-03) — live-verified contract, two record families

Zoho Desk joins as the fourth ticket source, and unlike SapphireIMS its contract is public *and* was **verified against a live instance** (`desk.zoho.in`, org `60001911841`, 629 articles) rather than inferred — which is what caught the three findings that would otherwise have shipped as bugs. (1) `limit` caps at **50**, not 100; a page size copied from the ServiceNow connector 422s on every call. (2) **No modified-since filter exists** (`modifiedTimeRange` is rejected as an extra query parameter), so incremental sync is a newest-first walk on `sortBy=-modifiedTime` with an early stop, not a server-side window. (3) Records sharing a `modifiedTime` arrive **id-ascending** inside the time-descending sequence, so the ServiceNow-style `(time, id)` compound cursor does not describe this API at all — it would trip the ordering guard on every call, or stop mid-tie and skip the rest of a bulk edit permanently. The checkpoint is therefore a timestamp plus the set of ids already emitted at it (`MAX_BOUNDARY_IDS` 500; overflow re-delivers, never skips). Descending order is also what makes offset paging safe here: a record edited mid-walk shifts rows *later*, so concurrency can only re-deliver — the inverse of the ascending-offset hazard that made the ServiceNow connector refuse `sysparm_offset`. Also live-verified and fixed during testing: the API root needs `/api/v1`, whose omission returns a bare `404` that reads exactly like a missing scope.

The connector carries **two record families from one source**, which no previous connector did: `tickets` (description + resolution merged, `ticketNumber` registered as the quotable identifier distinct from the opaque 18-digit row id, threads *and* internal comments merged at hydration) and `articles` (the KB — bodies only exist on the per-record detail call, so sync issues one, bounded and degradable). Articles are stamped `evidence_type: "kb_article"`, which routes them to the heading-aware document chunker and to **document** synthesis authority rather than ticket authority — a general "how the VPN works" page must not outrank the incident record on incident-specific fields. Both shared resolvers (`chunkers/registry.get_chunker`, `extraction_tasks.resolve_synthesis_role`) learned evidence-type awareness additively; attachment resolution for existing sources is deliberately unchanged. HTML → heading-preserving text runs on `html.parser` alone (no new dependency). `zoho_desk_reference_service` maps product → `business_service`, team → `assignment_group`, account → `customer_account`, KB category → `knowledge_category`, tags → topics, and related-ticket ids → symmetric case-link keys; shared infrastructure is never a case-link key (mass-merge guard), and relations stay untyped because Zoho exposes no relation semantics.

**Scope is the operational trap.** Zoho's OAuth grant is per-module and fails as `403 SCOPE_MISMATCH`, so a token with only `Desk.articles.READ` syncs the KB perfectly and contributes zero tickets. Discovery skips an unreadable module rather than aborting (verified necessary — the live token is exactly that partial grant, and aborting would have offered nothing from a portal with 629 syncable articles); `validate_credentials` names what was and was not granted; `probe_configuration` reports the granted scope string, per-module readability, counts, and whether detail calls return a body.

**Not built, honestly:** conversational bridging of Zoho ticket numbers — Zoho numbers are bare integers and the shared token regex deliberately never matches those (`order #12345 is unrelated` is an explicit assertion in `test_ticket_bridging.py`); the ticket's own registration and primary membership work, only the "Teams message quoting #4021" direction does not. Widening the shared regex would also match order numbers and hex colors, so it is a product decision; the narrower fix is resolving numeric candidates against registered identifiers inside `bridge_conversational_mentions`, which already has `db` + `tenant_id`. Also deferred: attachment *bytes* (metadata only, under `attachment_refs` — downloading is a bandwidth/retention decision, and filing metadata under `attachments` would look like support while `register_attachment_artifacts` registered nothing); CMDB topology (Zoho Desk has none). **Ticket-side behaviour is covered by tests but unverified against live ticket data**, because the available token lacks `Desk.tickets.READ` — run `probe_configuration()` after granting it before trusting a first production sync. Full design rationale: [ZOHO_DESK_CONNECTOR.md](./ZOHO_DESK_CONNECTOR.md).

## Resolved: SapphireIMS connector (2026-08-01) — config-mapped contract

SapphireIMS's REST API is real but its endpoint contract is **not public** (research: the documented pieces are the auth model — API key + auth token + submitted-by — and the Project/Service/Category ticket concepts; api.sapphireims.com is a JS portal, details are instance documentation). Hard-coding a guessed contract would produce a connector that looks finished and silently fetches nothing, so `connectors/sapphireims/` is **config-mapped**: endpoint paths, query-param names, and payload field names come from `source_config["api"]` / `["fields"]` with defaults modeled on the documented concepts, projects declared explicitly (`source_config["projects"]` — no public list endpoint), and `validate_credentials` probing the configured path so a wrong mapping fails loudly at setup. **Operators must verify the defaults against their instance's API guide before first sync.** Everything around the mapping is real: ServiceNow-grade retry/backoff, bounded pagination with a max-seen cursor (oversized backfills continue via incremental), kind-prefixed threads on the shared vocabulary (change-risk/verification discriminate SapphireIMS records like any other source), tolerant timestamp parsing (ISO / space format / epoch s / epoch ms), and reference enrichment (`sapphireims_reference_service`): related tickets → symmetric case-link keys + generic `related_ticket` edges (relation *types* aren't public — a guessed `caused_by_change` would poison change-risk, so edges stay untyped), CI/service names → entities with `affects_ci`. Not built, honestly: thread hydration (conversation endpoints undocumented — no-op like alert rollups), reverse healing (untyped relations; case links already tie both sides), typed causal edges (needs instance relation-type documentation).

## Resolved: Jira SM reference edges (2026-08-01)

The ServiceNow Phase 1 pattern applied to what Jira Service Management exposes universally. Connector (`connectors/jira_sm/connector.py`): issue events now carry slimmed issue links (with the linked issue's type — the v3 response embeds it), parent, components, labels, resolution, and optionally the JSM affected-services custom field (`source_config["service_field_id"]`, validated to a `customfield_` id); thread ids are **kind-prefixed** (`incident:PROJ-123`, kinds normalized to the ServiceNow vocabulary) so change-risk and post-action verification discriminate Jira record kinds with zero new code; incremental sync gained bounded pagination (the single-page fetch silently dropped >100 updates/tick) and a JQL-safe minute cursor with a 30-min overlap rewind — the previous full-ISO cursor was not even valid JQL, so incremental sync broke on the first non-default checkpoint (latent pre-existing bug). Set the integration account's timezone to UTC. `services/jira_reference_service.py`: linked issue keys become symmetric case-link keys ("is caused by" a Change emits `caused_by_change` — the exact edge change-risk counts; symmetric link types emit from one side only so edges never double); components/services become `business_service` entities with `affects_ci` edges (namespaced external ids, `external_system="jira_sm"` via the generalized `_ensure_entity`); reverse healing with an issue-key validator. `resolve_ci_entity`'s name path is now system-agnostic so change-risk finds Jira components; `lookup_topology` refuses non-ServiceNow entities (`topology_unsupported_for_source`) — a Jira id must never reach a sysparm_query. Parity fixes (2026-08-01, same day): `_jira_get` gained the ServiceNow-style retry/backoff (3 attempts, Retry-After honored — one 429 previously failed the whole sync task), and Resolves-style link types now emit `remediated_by_change` toward Changes (built-in "Resolves" + `source_config["resolves_link_names"]`, with a deny-list so configured names can never hijack built-in cause/duplicate semantics). Known limits: pre-existing bare-key Jira threads fragment on next re-delivery (new kind-prefixed threads start; old ones remain readable); no assignment-group equivalent (people never correlate by design); JSM Operations alerts (Opsgenie-heritage API) and Assets topology (Premium, separate API) are out of scope and would be their own connectors.

## Resolved: post-action verification (2026-08-01, migration `0036`)

`PlaybookVersion.verification_policy` promised "re-check telemetry 30 min post-action" since its introduction — nothing consumed it. Now `services/execution_verification_service.py` + the `evaluation.verify_executions` beat sweep (15 min) re-check completed success/partial runs after the policy's `recheck_after_sec` (default 1800, floor 300): the session's recorded entities resolve to CI rows (Layer C's exact-name contract), and post-completion signals on those CIs — new incident threads and new alert batches via `affects_ci` edges — decide the verdict. Alert-only verdicts are confirmed against the batches' own `last_event_time` so state-change re-deliveries (closing storms after a good fix) can't fail verification falsely; unreadable payloads count toward failure (attention, not false pass). Verdicts persist on `execution_runs` (`verification_status` verified/failed/unverifiable, `verified_at`, `verification_details` — the partial index is the sweep queue) and surface in the API response. `auto_close_on_success` emits `execution.auto_close_recommended` — it recommends, never closes a human's session. Runs with no session or no resolvable CIs are `unverifiable`, recorded honestly.

## Resolved: chunk search-side rollup (2026-08-01)

Chunks were written (0030 pipeline, halfvec index in 0032) but never read by any search path — semantic search hit parent embeddings only. Now `search/vector_search.py` implements CHUNKING_DESIGN §6: oversampled chunk ANN (80), **MMR at the chunk level** (`search/chunk_rollup.py`, λ=0.7, numpy similarity matrix) so near-duplicate chunks across evidence rows of the same thread can't crowd out distinct threads, then **rollup to one hit per parent** scored by its closest chunk — merged with a parent-embedding pass so unchunked evidence still surfaces (shared cosine space, scores merge directly). Results are `(EvidenceItem, distance, best_chunk|None)` — the hybrid ranker's `row[0]`/`row[1]` indexing is preserved, and `best_chunk` carries chunk id + `parent_section` breadcrumb + snippet for context rendering. Both passes now enforce search-surface visibility (legal hold, pending redaction, excluded access policies) — the parent pass previously filtered access policy only, so this is a deliberate tightening. The playbook-scoped variant got the same treatment; the hybrid ranker's corpus scoring is chunk-aware with zero interface change.

## Resolved: CI workflow (2026-08-01)

`.github/workflows/ci.yml`: two required jobs — backend pytest (Python 3.12, `pip install -e .[dev]`; the whole suite runs without live services, so no containers) and frontend vitest (Node 20, `npm ci`) — plus a ruff job — **a required gate since 2026-08-01**, when the 367-finding lint debt was cleared (369 at cleanup time): ~220 auto-fixed, ~110 long lines hand-wrapped, and the remainder resolved individually (a SQLAlchemy `== True` → `.is_(True)`, dead assignments removed, PEP 695 generics, a real `TYPE_CHECKING` import for a string forward-reference). Deliberate exceptions carry their reasons: per-file E501 ignores for prompt/seed data strings and one docstring table; a global N818 ignore for four released exception classes whose rename would break catch sites; two `noqa: UP042` because StrEnum changes `str(member)` semantics. Triggers: pushes to `main` / `feature/maf-context-graph-integration` and all pull requests, with per-ref concurrency cancellation.

## Resolved: domain-safe pattern mining (2026-08-01)

`pattern.cluster_episodes` previously fetched ALL of a tenant's approved episodes ("domain filter is intentionally removed"), clustered them tenant-wide, and stamped the synthesized pattern with whatever domain the API passed — the `/patterns/cluster` fallback even picked the tenant's FIRST domain. Since patterns are LLM-synthesized *content* surfaced through the projection's domain predicate, that put domain B's episode text inside domain-A-visible knowledge. Now: (1) mining is strictly scoped — a domain pass sees only that domain's episodes, the global pass (domain_id=None) sees only tenant-global ones (NULL episodes are deliberately NOT folded into domain passes: whichever pass ran first would capture them, making the tagging arbitrary again); (2) the no-domain API dispatch runs one pass per tenant domain plus a global pass instead of the arbitrary-first fallback; (3) `create_pattern_from_episodes` enforces domain-homogeneous membership as defense in depth (`DomainMismatchError` → 400): domain-D patterns accept D or tenant-global episodes, NULL-domain patterns accept only tenant-global ones, cross-tenant probes get the same "does not exist" as missing ids; (4) the manual discovery endpoint derives the pattern domain from the SET of episode domains (row order no longer decides between success and 400). **Caveat: patterns created before this guard may already contain cross-domain members — re-mining or a one-time review of existing patterns is needed to clean historical data.** Decision-pattern mining (`evaluation.mine_decision_patterns`) still aggregates tenant-wide, deliberately: it emits counts and failure rates into operational events, not synthesized content.

## Resolved: playbook steps in the agent projection (2026-08-01)

A playbook node in the agent projection previously carried only title/description facts — the agent knew a playbook existed but not what it does, forcing a second round-trip or a guess. `hydrate_nodes` now batch-loads each visible playbook's current version (one query per projection) and `playbook_version_facts` renders it bounded into the node facts: ordered step labels (15 max, 200 chars each, total count reported), flattened trigger conditions (600-char budget), rollback notes (300 chars), semantic version; `playbook_confidence` becomes the node confidence. Safety: a `current_version_id` pointing at another playbook's version is never surfaced (`playbook_id` check — also covers cross-tenant corruption, since `playbook_versions` has no tenant column); corrupt non-list steps degrade to empty, never a TypeError. The selector's character budget counts the enriched facts via `model_dump_json`, so steps cannot blow the projection budget.

## Resolved: change-risk assessment (2026-08-01, Phase 4 — SLA priors deferred)

`services/change_risk_service.py` composes Phases 1-3 into a deterministic, explainable risk profile per CI: distinct change records on the CI (`affects_ci` edges, record kind from the thread-id prefix) versus those blamed for incidents (`caused_by_change` edges — human-written references, not inference), incident pressure and alert-rollup activity in the window (default 180d, max 730d), and the cached-topology blast radius (dependency edge types only — `contains` is composition, not dependency). Transparent additive scoring; the `factors` list is the explanation. Exposed as the `assess_change_risk` MAF tool (read-only client port), and `GET /api/v1/graph/change-risk`. Coverage honesty is built into the payload: the `topology_note` states dependents come from the cached working set and whether the CI's topology was ever fetched.

**SLA priors are deferred, deliberately.** `task_sla` rows are per-task metrics, not content — ingesting them as evidence would pollute the evidence pipeline (embeddings of numbers, retention of metrics), and ranking integration needs a metrics side-channel joined at scoring time. The right shape is a small `task_sla` enrichment column or table populated at sync and consumed by the ranker — schema work that should ride the next ranking-calibration effort, not be bolted onto evidence ingestion.

## Resolved: em_alert rollup ingestion (2026-07-31, Phase 3)

Alerts are ingested **rolled up per (CI, UTC day)** — never one evidence row per alert, so embedding spend, retention, and ANN quality stay bounded on noisy environments. `connectors/servicenow/alert_rollup.py` groups each sync invocation's alerts and emits one event per group: counts, severity distribution, worst-severity label, up to 30 sample lines (symptom vocabulary for the embedding), the event-time window, the CI reference in the exact shape the Phase 1 extractor consumes (entity + `affects_ci` edge + topology warm candidate for free), and up to 20 promoted-incident sys_ids. Those incident references become typed `preceded_incident` edges only — **deliberately never case-link keys**, since one rollup can reference several unrelated incidents on a busy CI and 1.0 links would merge their canonical cases. Severity is filtered server-side (`severity<=3` default, `source_config["alert_severity_max"]`), applied to every `^NQ` branch of the sysparm query; the keyset checkpoint still advances on raw alert rows so batching cannot skip records. Rollup threads skip journal hydration. Operational notes: existing sources pick up `em_alert` on their next discovery run; groups spanning sync invocations produce multiple evidence rows in one thread (episodes aggregate them); unassigned-CI alerts pool into one bucket per day. Still open: SLA priors (see the Phase 4 note).

## Resolved: CMDB topology hybrid (2026-07-31, Phase 2)

Deliberately NOT a bulk CMDB sync — ServiceNow stays the system of record. `services/cmdb_topology_service.py` fetches a CI's ±1-hop neighborhood live (`cmdb_rel_ci` + `cmdb_ci`, two API calls, bounded at 200 relationships) and write-through-caches it into the existing `entities`/`graph_edges` tables (parent -[`depends_on`/`runs_on`/`hosted_on`/`contains`/`uses`/`connected_to`]-> child; unmapped labels become `related_to` with the raw label in edge metadata). Freshness is TTL-based (`Entity.last_synced_at`, 7 days): re-fetches end-date upstream-deleted relationships via `GraphEdge.valid_to`. Reachable three ways: the `cmdb_topology` MAF tool, `GET /api/v1/graph/cmdb-topology`, and `evaluation.warm_cmdb_topology` (dispatched post-commit by the correlate task when a ticket references a stale CI — so the agent projection traverses the operational working set with no runtime round-trip). Lookups within 5 minutes serve the cache; ServiceNow outages fall back to the cached view explicitly marked stale. Known limits: only the working set is cached (correlation/blast-radius sees cached topology, not the full CMDB), hub CIs truncate at 200 relationships, and there is no HTTP `CmdbTopologyClient` yet — HTTP-deployed MAF agents use the REST endpoint directly.

## Resolved: ServiceNow reference-field enrichment (2026-07-31, Phase 1)

The connector previously discarded the reference fields that carry ServiceNow's human-verified record graph. `TABLES` now requests `problem_id` / `rfc` / `caused_by` / `parent_incident` / `cmdb_ci` (+ dot-walked name/class) / `assignment_group` (+ name) / `close_code` / `category`, and `services/servicenow_reference_service.py` turns them into: (1) symmetric case-link keys — incident↔problem↔change correlate at 1.0 regardless of ingestion order, with an incident cluster under one problem forming one canonical case by design; (2) typed evidence→evidence graph edges (`related_problem`, `caused_by_change`, `remediated_by_change`, `child_of_incident`) with reverse healing via case-link rows when the referenced record ingests later; (3) CI and assignment-group entity rows on the `(entity_type, external_system, external_id)` natural key, so seed resolution matches "vpn-gw-east-01" exactly and traversal reaches everything that touched the CI. cmdb_ci/assignment_group are deliberately excluded from case-link keys (shared infrastructure would mass-merge unrelated cases). Enrichment runs inside a SAVEPOINT after correlation — fail-soft, never poisons the transaction. Still open here: reverse healing covers the first referencer only — later referencers heal on their own next update.

## Resolved: playbook semantic seeds (2026-07-31, migration `0035`)

Playbooks previously had no embedding, so the agent seed resolver could only reach one directly via title/description FTS — weak for symptom-level language and empty on cold-start tenants. `playbooks.embedding` (Vector(3072), halfvec HNSW expression index) is now written best-effort on candidate generation, version creation, and title/description updates (`services/playbook_embedding.py`; text = title + description + published version's trigger conditions + step titles), and the seed resolver's semantic layer matches playbooks directly alongside episodes (same query embedding, approved-only, 0.5 similarity floor). Pre-0035 playbooks stay NULL until the ad-hoc `evaluation.backfill_playbook_embeddings` task runs — invoke once per environment after upgrading.

## Still open after the 2026-07 shipment

- **Doubled braces in pre-existing system prompts — RESOLVED 2026-08-03 (backlog E7)**: `decision`/`pattern`/`playbook` v2 prompts registered as defaults with single braces; v1s stay immutable for eval baselines (identity/episode families were fixed earlier).

- **LLM provider resilience — RESOLVED 2026-08-03 (backlog E1)**: 120s per-call timeout, per-model in-process circuit breaker (5 consecutive failures → 60s open, single half-open probe), and optional one-shot fallback via `settings.llm_fallback_model` (usage recorded against the serving model). The breaker is per-worker by design — no cross-process coordination.
- **Prompt-injection fencing at ingest extractors — RESOLVED 2026-08-03 (backlog E2)**: episode/decision/identity/pattern extractors now wrap untrusted content in `<untrusted-evidence>` markers with a data-not-instructions notice at the formatting layer (registered prompt versions stay immutable); embedded closing markers are neutralized. Identity ADJUDICATION passes short structured JSON fields (names/aliases), not raw bodies — out of this scope by design.
- **Ranking calibration — PARTIAL 2026-08-03 (backlog E3)**: quality_score now computed from reviewed playbook confidence + query-specific evidence support; abstention threshold (0.35, overridable per call); version lookups batched (one query). Still open: per-playbook graph/identity/negative scoring queries remain per-candidate, and SLA priors (deferred from change-risk) still await the metrics side-channel.
- **Sync single-flight — RESOLVED 2026-08-03 (backlog E4)**: transaction-scoped Postgres advisory lock per source object; a second worker gets `skipped_locked` instead of racing checkpoint writes. Lock releases automatically at commit/rollback — a crashed worker cannot leak it.
- **Identity review queue UI — RESOLVED 2026-08-03 (backlog E5 slice)**: the Review Queues console (`/suggestions`) now lists `needs_review` identities with resolve/deactivate actions. Broader admin-console coverage (role-binding CRUD, retention console) remains API-led.
- **Execution engine depth** — tool registry, rollback execution, cancellation and resume remain Release-2 scope. Shipped from its safety list: telemetry-based outcome verification (2026-08-01) and stale approval expiration (2026-08-03, E6 slice — 72h pending approvals expire on the verification beat; expiry never approves).
- **Reply-inheritance ordering — RESOLVED 2026-08-02 (backlog A10)**: debounced reconstruction now re-attempts inheritance for un-anchored teams replies in the cluster (`_reconcile_reply_inheritance`), carrying every shipped guard (single-case parent, dissociation veto, thread negation).
- **Dissociation veto phrase list — UPGRADED 2026-08-02 (backlog A1)**: the message-function classifier now decides when confident (both directions: paraphrase vetoes, false phrase hits rescued); the phrase list remains the deterministic floor for unlabeled/low-confidence rows. Bot replies now inherit at reduced confidence and bot prose never anchors (A6, 2026-08-03). Still open: a digest-downgraded `mentioned_only` row blocks a later `reply_inheritance` upgrade (first-writer-wins, conservative).
- **Suggestion queue volume — RESOLVED 2026-08-03 (backlog C4)**: per-tenant pending cap (500) pauses generation until reviewers drain the queue, and the Review Queues console (`/suggestions`) covers both semantic suggestions and fleet groups. Identity review queue console remains open (E5).
- **Wrong-source-attribution rate — RESOLVED 2026-08-03 (backlog C5)**: the evaluation harness gained the `episode_citation` dataset kind (per-step gold citations, pinnable `episode_prompt_version`), reporting mean unsupported-step and wrong-attribution rates per run — v2 vs v3 comparisons are now a dataset away. Curating the gold datasets themselves is operator work.
- **Episode contradictions are review-surface only — RESOLVED 2026-08-03 (backlog C6)**: episode facts in the agent projection now carry a bounded contradictions block (3 entries, truncated claims); the review surface keeps the full record.
- **D1/D2 external-dependency skips (recorded 2026-08-03 per the M6+D/E goal)** — the tractable D1 slice shipped (request-type/change-window customfield mapping, sync page-order guard); the rest awaits access that does not exist in this environment: **Opsgenie alerts connector** (needs Opsgenie credentials), **Jira Assets topology** (Premium API), **Confluence KB ingestion** (no instance to verify the contract against — a SapphireIMS-style config-mapped blind build is possible when wanted), and the **AutomationEdge connector** (no AutomationEdge access; also blocks its trait/authority roles from Doc-3/P4).
- **SLO + business impact modeling** — still Release 3 scope. (The rest of the old "telemetry/topology/alert/change-event ingestion" line shipped 2026-07/08: CMDB topology hybrid, em_alert rollups, change reference edges — see the resolved sections above.)

## Adding a new connector type

Built-in types `teams`, `gmail`, `servicenow`, and `jira_sm` are registered in `backend/src/contextedge/connectors/registry.py`. New vendors still need a class under `connectors/` and an entry in the registry map.

## Sync requires a worker on the `sync` queue

`run_backfill` and `run_incremental_sync` in `workers/sync_tasks.py` route to the `sync` Celery queue. Local development includes `sync` in `DEFAULT_QUEUES` in `backend/dev.py`. Custom workers that omit `sync` will leave retry and backfill tasks stuck.

Fix direction: include `sync` in consumed queues and verify worker routing against [`docs/RUNBOOK.md`](../docs/RUNBOOK.md).

## Sync overlap (resolved 2026-08-03, E4)

Single-flight per source object shipped (advisory xact lock; overlapping runs skip with `skipped_locked`). Evidence-dedup race was closed in migration `0026_dedup_uniqueness` — the normalize worker now catches `IntegrityError` from the partial unique index on `(tenant_id, content_hash)` and falls through to the existing-row path.

## JWT secret in non-development

Production-like environments must set a real `JWT_SECRET_KEY` when `APP_ENV` is not `development`.

## Role bindings are stored, but login currently flattens roles — P0 for multi-domain tenants

`RoleBinding` stores `scope_type` and `scope_id`, but the login flow in `api/v1/auth.py` currently selects only `RoleBinding.role` values when it builds the JWT. In practice, most route enforcement is role-name based, with finer scope coming from token claims such as `allowed_domain_ids` or `workspace_ids`, not from dynamic resolution of every role binding on each request.

The 2026-08-05 external review escalated this to an enterprise launch blocker, and validation against the code confirms the mechanics: `deps.has_role()` is a pure role-name check, so a reviewer or domain admin bound to ONE domain is treated as holding that role tenant-wide by every route-level `require_role` call. The required shape is effective grants as `(role, scope_type, scope_id)` enforced through a shared authorization layer, with negative tests per sensitive route and cross-domain combination. This is an architectural change (JWT claims, a policy layer, and route migration), not a spot fix — deliberately **not** attempted as part of the review-response patch set, because a partial scoping change that some routes honour and others don't is more dangerous than the documented current state. Until it lands, single-domain tenants are unaffected; multi-domain tenants should treat role grants as tenant-wide regardless of the binding's scope fields.

## Frontend source onboarding is local-file first

The Add Source dialog is strongest for local directory ingest through `/sources/local-ingest`. The backend contains connector modules for Gmail, Teams, ServiceNow, and Jira Service Management, but the current dialog does not expose a full credential and connector-configuration experience for those cloud connectors.

## Admin console coverage is partial

The Settings page can show tenant data, list users, and create workspaces or domains, but it is not yet a complete admin console. User creation, role binding CRUD, edit or deactivate flows for workspaces and domains, and the retention console remain mostly API-led or placeholder UI.

## Policy assignment UI is partial

The dashboard currently surfaces source retention and classification assignment plus evidence access assignment. Generic policy-assignment listing and playbook approval-policy assignment exist in the backend, but they do not yet have a dedicated first-class dashboard workflow.

## Notifications are lightweight UI only

The current frontend notification experience is the header dropdown in `AppHeader`, backed by polling `/notifications` every 60 seconds. There is not yet a dedicated inbox page, live push transport, or workflow-routing console, even though the notification service abstraction already includes email and webhook channels.

## Operational events and retention jobs

`apply_retention_policy` and `purge_archived_evidence` in `retention_service.py` are production-ready services (legal-hold safe, dry-run preview, `limit`/`limit_reached` cross-tick drain), but neither is wired into Celery Beat. Tenant retention defaults have no effect until a cron trigger or operator script calls them. See [11-retention-and-operational-events.md](./11-retention-and-operational-events.md) for the two-phase archive → purge model; see "Scheduled jobs that need wiring" below for the tracked deferrals.

## Scheduled jobs that need wiring

These tasks are coded, tested, and safe to run — they're just not yet in `celery_app.beat_schedule`:

- **Retention archive (`apply_retention_policy`)** and **purge (`purge_archived_evidence`)** — per-tenant memory-class archive, then hard-delete or soft-purge past the configured `archive_grace_days`. Wire when the customer confirms their desired cadence (typical: archive daily, purge weekly).
- **Weekly golden eval regression** — `backend/evals/run_regression.py` runs today manually or in CI; the weekly Beat entry is deferred until the customer signs off on what pass bar (absolute accuracy? week-over-week delta?) should trip an alert.

## Object storage blobs are not lifecycle-managed in-app

Raw payloads above the offload threshold are stored in S3-compatible object storage (MinIO) and referenced by `RawEvidenceObject.object_storage_key`. The application currently uploads and reads these blobs but does not delete them (no TTL, lifecycle policy enforcement, or garbage collection job in code). In practice, blob retention relies on external bucket lifecycle rules or manual cleanup.

## Graph Explorer is read-only

The Graph Explorer page (`/graph-explorer`) provides interactive visualization and traversal of the context graph — statistics, subgraph rendering via React Flow, and BFS neighbor browsing — but does not yet support creating, editing, or deleting graph edges from the UI. All graph mutations happen through backend services: builder functions called from pattern discovery, playbook generation, contradiction scans, identity linking, decision extraction, and episode graph construction.

## Decision extraction depends on LLM quality

AI-extracted decisions (Tier 1) rely on `decision_extractor.py` prompting an LLM to identify operational actions from evidence text. Decision types are open-ended labels, not a fixed enum, which means analytics and filtering may require normalization or fuzzy matching across label variations. The extractor truncates input to 4,000 characters; decisions mentioned later in long evidence items may be missed. Governed decision edges (Tier 2) from execution service are high-fidelity and not subject to this limitation. First-class decision traces (Tier 3) provide the richest representation — see [16-decision-traces.md](./16-decision-traces.md).

**Partially mitigated by chunking (2026-05-08, migration `0030`):** the chunking pipeline now writes per-chunk rows under 1,500 chars each, so retrieval can surface specific chunks past the original 4 KB cap even though the extractor itself still operates on the parent body. The full fix is per-chunk decision extraction — running `extract_decisions` against each chunk and deduping decisions across chunks of the same evidence — which is a follow-up that lands alongside the search-side rollup. Until then, the cap remains in effect for the extraction pass, but retrieval does not lose the content.

## Resolved: Human-in-the-loop rejection now uses structured reason codes

Previously, `DecisionOption.rejection_reason` was free-text only, which meant rejection signal was unaggregatable and `get_decision_effectiveness` couldn't break out failure modes. Migration `0017_rejection_modification_codes` adds:

- `decision_options.rejection_code` (one of `REJECTION_REASON_CODES`: `wrong_diagnosis`, `plan_incomplete`, `needs_human_judgment`, `user_context_missing`, `policy_violation`, `other`)
- `decision_outcomes.feedback_code` (same enum) and extends `OUTCOME_RESULTS` with `"rejected"` so analytics can separate reviewer-rejected from executed-and-failed decisions.
- `approval_requests.modification_diff` JSONB + `modification_reason_code` for the Modify branch of the Approve / Modify / Reject flow.

A new `POST /decisions/{id}/reject` endpoint (`services.decision_trace_service.reject_decision`) writes the structured code, creates a `DecisionOutcome(execution_result="rejected")` with a `resulted_in` graph edge, flips `decision.status="superseded"` + `human_override=true`, and emits a `decision.rejected` operational event. The free-text `rejection_reason` / `feedback_received` fields remain for the `other` + write-in case. See [16-decision-traces.md](./16-decision-traces.md) for the structured-code walkthrough.

## Resolved: Reviewer console bundle endpoint

`GET /api/v1/review-queue/{session_id}/context` (`services.review_queue_service.build_review_context`) composes session + top-pending decision + similar-decision aggregate + scoped decisions / execution runs / operational events into a single response (`ReviewQueueContext`) so the reviewer UI renders in one round trip instead of fanning out. `top_decision_badge.level` is derived server-side (`green >= 0.8`, `amber 0.5–0.8`, `red < 0.5`) so thresholds can't drift between consumers. Paired with `GET /decisions` confidence filter/sort (`min_confidence`, `max_confidence`, `sort=confidence_desc|confidence_asc|created_desc`) this unlocks queue-based prioritization.

## Resolved: Review-queue bundle prefetched to Redis on session creation

The bundle endpoint is read-through cached (`review_queue:{tenant_id}:{session_id}`, TTL 300s) and pre-warmed by the `prefetch_review_context` Celery task enqueued from `create_resolution_session`. This closes the sub-2s first-render budget for the reviewer console — the click-to-render round trip hits Redis, not Postgres. Cache is shape-safe (default limits only, custom limits bypass), tenant-scoped, corrupt-entry tolerant, and can be bypassed per-request with `?no_cache=true`. Enqueue failures are logged and swallowed so a degraded Celery broker never blocks session creation.

## Partial: Reviewer console — Phase 5

`/review` route renders zones 2 (ticket header), 3 (raw user message), 5 (ranked hypotheses with ruled-out reasons + similar-decisions aggregate), and 7 (**Approve / Modify / Reject** — all three verbs live). Queue pane consumes `/decisions?status=pending&sort=confidence_desc` with confidence-badge color levels (`green ≥ 0.8`, `amber 0.5–0.8`, `red < 0.5`) matching the server-side thresholds.

**Modify flow** opens a dialog pre-filled with the pending approval's current step inputs as editable JSON. Reviewer provides a required summary (becomes `modification_diff.summary`, which the backend uses as the modified step's action label on the new `Decision` option), optional free-text comment, and a reason code from the same 6-code enum as reject. Submission POSTs `{modification_diff: {inputs, summary}, modification_reason_code, comment}` to `/execution/runs/{run_id}/approvals/{approval_id}/modify`. TanStack Query invalidation refetches the bundle; the backend's `invalidate_review_context` (wired into `decide_approval`/`modify_approval` transitively via `create_decision`) drops the Redis cache too.

**Known limitation of the Modify UI:** uses a raw JSON textarea for the `inputs` editor. This preserves the backend's schema-less flexibility (any step shape can be modified) but is a rough reviewer UX. Typed per-step forms — keyed on `PlaybookStep.tool_ref` or `step_title` — are a clean follow-up. For reviewers working on well-known step shapes (cert renewal, password reset), these forms would be materially faster.

**Still deferred:**
- **Zone 4 evidence cards** — bundle does not carry evidence; needs a `/decisions/{id}/provenance` fetch per top-decision rendered with `delta_signal` color and `baseline_ref.comparison_label`.
- **Zone 6 plan steps** — needs joining `PlaybookVersion.steps` (the M2 schema) + `verification_policy` so reviewers see reversibility, time estimate, per-step safety class, and the auto-close-on-recheck commitment. Requires a playbook-version fetch (or a dedicated "step detail" endpoint).
- **Bulk approve** — described in the design doc (filter to confidence > 0.85, select-all, one-click approve with condensed preview modal). Backend supports it today via the existing Approve endpoint; UI affordance is not built.
- **Keyboard shortcuts** — `A`/`M`/`R` for verbs, `J`/`K` for queue navigation.
- **Typed Modify forms** — see limitation above.
- **Frontend tests** — no test runner is configured for the frontend package (`npm test` stubs out). Add one alongside the next slice.

## Resolved: LLM cost observability + Week-1-2 cost wins

Four issues flagged in [`ENTERPRISE_ARCHITECTURE_REVIEW.md`](../ENTERPRISE_ARCHITECTURE_REVIEW.md)'s Weeks 1–2 roadmap are now shipped:

1. **Prompt caching** — `ai/provider.py::llm_complete` splits messages into a stable system block (marked `cache_control: {"type": "ephemeral"}` via `ai/observability.build_messages`) and a dynamic user block. OpenAI's automatic prefix cache and Anthropic's ephemeral cache both hit once the system prompt warms per worker. Classifier prompt rewritten accordingly.
2. **Classify-before-embed** — `workers/extraction_tasks._normalize` runs relevance classification inline before embedding + identity + decision extraction. Items scoring `not_relevant` with confidence ≥ 0.75 skip the downstream LLM fan-out entirely. `classify_relevance_task` is no longer part of the default fan-out (still available for manual re-classification from the admin UI / attachment extraction path).
3. **Per-call token + cache logging** — new `ai/observability.py` emits Prometheus counters (`contextedge_llm_tokens_total`, `contextedge_llm_requests_total`) tagged with tenant/model/task/token-type/outcome, a structured `llm.usage` log line per call, and an `OperationalEvent(event_type="llm.usage")` for historical dashboard queries. Both `llm_complete`/`llm_complete_json` and `generate_embedding`/`generate_embeddings_batch` instrumented.
4. **HNSW indexes on embedding columns** — **corrected 2026-07-29:** migration `0021` could never build these indexes — pgvector's HNSW caps the `vector` type at 2,000 dimensions and the app stores 3,072, so every similarity query was a sequential scan despite this entry previously claiming otherwise. Real ANN indexing landed in migration `0032_halfvec_hnsw_indexes`: HNSW *expression* indexes over `(embedding::halfvec(3072))` on `evidence_items`, `evidence_chunks`, `decisions`, and `episodes` (requires pgvector server extension >= 0.7; the migration no-ops with a notice on older extensions). Query side goes through `search/vector_ops.py::halfvec_cosine_distance` — any direct `column.cosine_distance(...)` ordering will not use the index.

Also shipped in the same slice: `GET /api/v1/admin/llm-usage` + `/admin/cost` reviewer UI that renders per-tenant spend, cache-hit rate, and top-N model×task breakdown. Gated to `tenant_admin` / `platform_super_admin`. Refetches every 60 seconds.

## Resolved: Weeks 3-4 — quadratic scanner / retention / episode chunking

- **Contradiction scanner redesign.** `services/contradiction_service.scan_contradictions` now uses HNSW top-K KB candidates + incremental cursor (new `contradiction_scan_state` table, migration `0022`) + explicit `max_llm_calls` budget. Result dict reports `llm_calls_used` / `token_skips` / `cursor_skips` / `budget_skips` / `budget_exhausted`. Expected 80-95% LLM-call reduction on warm tenants.
- **Retention hard-delete + soft-purge.** New `purge_archived_evidence(mode="hard_delete"|"soft_purge", dry_run, limit)` in `services/retention_service.py`. Hard-delete cascades via FK to `attachment_artifacts` / `correlation_edges` / `contradiction_scan_state`; soft-purge NULLs embedding + body and replaces title with `"[purged]"`. Legal hold is in the SQL `WHERE` clause (never post-filtered). Beat scheduling deferred — see "Scheduled jobs that need wiring" above.
- **Episode extractor chunking.** `ai/extractors/episode_extractor.reconstruct_episode` now splits clusters larger than `MAX_ITEMS_PER_CALL=20` into per-chunk LLM calls; per-item body truncated at `PER_ITEM_CHAR_LIMIT=2000`. Logs `episode_extractor.chunked` on the split path so oversize clusters are observable.

## Resolved: Weeks 5-6 — enterprise gates

- **Shadow automation_mode.** `models/playbook.AUTOMATION_MODES` is now a validated enum `("suggest_only", "shadow", "human_confirmed", "supervised", "full_auto")`. `record_tool_invocation` detects shadow runs and tags outputs with `shadow: True`, forces status to `shadow_executed`, and fires `tool.shadow_executed` events so analytics can separate dry-runs from real outcomes.
- **Correlation-ID propagation.** Celery `before_task_publish` / `task_prerun` / `task_postrun` handlers in `workers/celery_app.py` thread `request_id` / `correlation_id` / `causation_id` through the HTTP → worker boundary. `llm.usage` structlog line also enriched so a single reviewer action is greppable across HTTP → Celery → LLM log lines.
- **Ingest-time redaction.** `services/redaction_service.py` regex MVP covers EMAIL / PHONE / SSN / CREDIT_CARD / AWS_ACCESS_KEY / AWS_SECRET_KEY / PRIVATE_KEY blocks. Wired into `_normalize` before the classifier / embedder / identity / decision extractor see anything. `content_hash` computed on the pre-redaction payload so future regex tuning doesn't break dedup. Gated by `settings.redaction_enabled` (default True).

## Resolved: Weeks 7-9 — scale foundations

- **Per-tenant LLM budget enforcement.** New `tenant_llm_budgets` table (migration `0023`) + `services/tenant_budget_service` + pre-call gate in `llm_complete` that raises `TenantBudgetExceeded` on `block` or emits `llm.budget_warning` on `warn`. Admin API `GET/PUT /admin/tenant-budget` + `GET /admin/tenant-budget/status` + `BudgetPanel` UI on `/admin/cost`.
- **Schema-validated LLM JSON.** `ai/provider.llm_complete_json_validated(prompt, schema)` accepts a Pydantic model, validates the parsed JSON, and on failure sends exactly one repair call with the raw prior response + validation errors + JSON Schema. Retry budget hard-capped at 1.
- **Evidence-table scale indexes** (migration `0024`): BRIN on `(tenant_id, ingested_at)`, partial B-tree on `(tenant_id, relevance_state)` for the reviewer queue, partial B-tree on `(tenant_id, updated_at)` for the retention purge sweep. All `CREATE INDEX CONCURRENTLY`. Full partition-conversion runbook deferred in `codewiki/04-evidence-normalization-and-storage.md` until customer volume numbers land.

## Resolved: Weeks 10-12 — agent quality

- **Decision calibration + pattern mining on Beat.** `evaluation.calibrate_decision_confidence` and `evaluation.mine_decision_patterns` (in `workers/decision_tasks.py`) accept the `"all"` sentinel for per-tenant fan-out with isolated exception handling and are scheduled daily.
- **Prompt versioning + per-tenant A/B.** New `ai/prompts/` package with `Prompt` dataclass + `register_prompt` / `get_prompt` / `resolve_version`. Per-tenant variants via `settings.tenant_prompt_variants_json`. `prompt_name` + `prompt_version` threaded through `llm_complete` into `llm.usage` events. All seven LLM prompt families register a `v1` default at import time: `relevance`, `episode`, `decision`, `identity`, `pattern`, `playbook`, `contradiction`.
- **Golden eval scaffold.** `backend/evals/` with `golden.jsonl` format + `run_regression.py` CLI (confusion matrix, non-zero exit on failure). Weekly Beat deferred — see "Scheduled jobs that need wiring".

## Resolved: Watch-list JSONB indexes

The two JSONB hot spots flagged in `ENTERPRISE_ARCHITECTURE_REVIEW.md` §5 are now indexed (migration `0025_jsonb_gin_indexes`):

- `ix_graph_edges_metadata_extra_gin` — future `metadata.reason = X` edge traversals hit an index.
- `ix_evidence_items_canonical_entity_refs_gin` — identity / decision / correlation filters that hit the JSONB blob are indexed, whole-column rather than `->'identities'` so `decisions` filters benefit too.

Both are `jsonb_path_ops` GIN indexes (smaller, faster for the `@>` containment operator current code uses) built `CONCURRENTLY`. Other JSONB columns (`context_snapshot`, `evidence_summary`, `baseline_ref`, `modification_diff`) remain un-indexed by design — add targeted GIN only when a specific filter path shows up.

## Resolved: Frontend production build unblocked

Two pre-existing SSR issues on `/review` and `/decisions` (both called `useSearchParams()` without a Suspense boundary) blocked `next build` once the `add-source-dialog.tsx` type error was fixed. Both pages now follow the standard Next.js 16 pattern: a thin default export that renders `<Suspense fallback={…}>` wrapping a `*PageContent` component that owns the hook call. `npm run build` is green end-to-end; both routes render as `○ (Static)`.

## Resolved: Semantic similar-decision retrieval

`Decision.embedding` (Vector(3072)) is populated inline during `create_decision` from `decision_type + compact_trace + rationale_summary`. `find_similar_decisions` and `find_similar_decisions_aggregate` accept `query_decision_id` (uses that decision's stored embedding) or `query_text` (embedded on the fly) and order results by `embedding <=> query` cosine distance. JSONB containment on `workflow` / `environment` / `impacted_dependency` remains as a structural pre-filter in both paths so structural scoping still works with semantic ordering. When no query embedding resolves (neither param passed, or provider failure), retrieval falls back to the pre-C3 `created_at DESC` ordering — no caller breakage. Embedding write failures at `create_decision` are swallowed; the decision lands with `embedding = NULL` and participates in structural retrieval until re-embedded.

**Update 2026-07-29:** `decisions.embedding` (and evidence/chunks/episodes) now has a halfvec HNSW expression index via migration `0032` — see the corrected HNSW entry above. Still open: no back-fill task exists to embed pre-C3 decisions; they'll stay embedding-null until re-written or a dedicated `reembed_decisions` task is built.

## Resolved: Cache invalidation on downstream mutations

`services.review_queue_service.invalidate_review_context(tenant_id, session_id)` is called from every mutation that changes a session's review state: `create_decision`, `record_outcome`, `reject_decision` (decision service), and `close_resolution_session` (session service). `decide_approval` and `modify_approval` in the execution service embed `create_decision`, so they invalidate transitively through that call — no duplicate wire-in.

The helper opens a short-lived `aioredis` client via `settings.redis_url`, deletes the key, and swallows transport errors (a degraded Redis never bubbles into mutation code paths). `session_id=None` is a no-op so call sites can invoke unconditionally.

**Known caveat:** invalidation fires post-flush but pre-commit, so a narrow race window exists where a concurrent bundle read could re-populate the cache with the pre-commit snapshot. The 300s TTL backstops the race. A `SQLAlchemy after_commit` hook is the cleanest fix if real-time correctness ever matters more than the current simplicity.

## Resolved: Evidence baseline / delta signal for Zone 4 cards

`EvidenceItem.baseline_ref` (JSONB) and `EvidenceItem.delta_signal` (`neutral` / `amber` / `red`) added in migration `0019_evidence_baseline`. Post-normalize, `compute_evidence_baseline_task` (`workers/evidence_baseline_tasks.py`, `extraction` queue) fans out alongside `classify_relevance_task` and `correlate_evidence`, matches prior evidence on tenant + evidence_type + source_object_id within a 7-day window, and records a relationship-only baseline: "last seen N days ago" or "first observation in 7d window". `delta_signal` defaults to `neutral`; connector-stamped richer signals are preserved.

**By design:** numeric deltas ("74% → 32% disk free") come from connectors that know the metric semantics — the generic worker only does relationship baselines. The JSONB shape is open-ended so connector-side and worker-side baselines coexist on the same column.

**Not yet wired:** no IT-telemetry connectors populate numeric baselines yet — the Intune / CrowdStrike / AD / Entra connectors are part of Phase 4. Until they land, Zone 4 cards render the relationship-only label, not numeric deltas.

## Resolved: Playbook step metadata — reversibility, time estimate, verification flag

`PlaybookStep` (`schemas/playbook.py`) adds per-step `reversible`, `time_estimate_sec`, `verification`, `rollback_hint`, `safety_class`, and `tool_ref`. All fields are optional with defaults so pre-M2 JSONB payloads keep validating, and `extra="allow"` preserves vendor-specific keys. Storage is the existing `PlaybookVersion.steps` JSONB — no column change.

Migration `0018_playbook_step_metadata` adds `playbook_versions.verification_policy JSONB` for the reviewer console's "auto-close on successful recheck" commitment (`VerificationPolicy`: `auto_close_on_success`, `recheck_after_sec`, `recheck_metric`, `recheck_source`). This backs the UI's trust-building promise that the agent closes its own loop rather than fire-and-forget.

**Not yet wired:** the execution engine does not yet honour `verification_policy` — the scheduler + recheck worker that re-evaluates `recheck_metric` after `recheck_after_sec` and auto-closes the session on success is a follow-up. Today the fields are descriptive only; the reviewer UI can render them but the backend does not act on them.

## Resolved: Approve / Modify / Reject flow — Modify endpoint is live

`POST /api/v1/execution/runs/{run_id}/approvals/{approval_id}/modify` (`services.execution_service.modify_approval`) accepts an `ApprovalModificationRequest` with `modification_diff`, `modification_reason_code` (same enum as reject), and optional `comment`. It flips the `ApprovalRequest.status` to `modified`, merges `modification_diff["inputs"]` into the step's inputs JSONB, transitions the run + step back to `running`, emits an `approval.modified` operational event, adds a `modified_by` graph edge, and creates a first-class `Decision(decision_type="modify")` with two options — original (`selected=False`, `rejection_code=<reason>`) and modified (`selected=True`) — keeping the graph's `considered`/`chose` invariant intact. `DECISION_TYPES` now includes `"modify"`.

## Resolved: Decision traces are now first-class graph citizens

Previously, decision traces were flat `DecisionTraceEvent` rows with no graph connectivity or structured option/outcome tracking. This has been addressed: `Decision`, `DecisionOption`, and `DecisionOutcome` models are fully integrated into the context graph with typed edges (`based_on`, `considered`, `chose`, `applied_policy`, `required_approval`, `resulted_in`, `followed_by`). The execution service creates first-class decisions at every key lifecycle point (playbook start, approval/denial, completion). A dedicated `/decisions` API and frontend page provide full CRUD, chain navigation, similarity search, and effectiveness analytics. The flat `DecisionTraceEvent` is retained for backward compatibility as a compact audit trail.

## Decision and identity linking order in normalization

The normalization worker runs `link_evidence_identities` before `link_evidence_decisions`. Both write to `evidence.canonical_entity_refs` non-destructively (using separate keys: `identities` and `decisions`). If either step fails, the other's data is preserved. However, if identity linking is re-run after decisions have been written, the merge logic in `link_evidence_identities` preserves existing keys — but a full re-normalization should be monitored to ensure both keys remain intact.

## Thread hydration requires normalization to run first

`Thread` rows are created during normalization via `ensure_thread_for_evidence`. If normalization has not yet processed a raw evidence object, the corresponding `Thread` row will not exist and the hydration API will return 404. This is by design (threads are created lazily), but operators should be aware that hydration depends on normalization completing first.

## Gmail backfill checkpoint seeds history_id for incremental

Gmail's `backfill` fetches the mailbox `historyId` when the last page completes and stores it in the checkpoint. This bridges backfill to incremental sync. If a backfill is interrupted before the final page, only a `page_token` checkpoint exists and incremental sync will fail until backfill finishes.

## Historical note: sync Celery tasks

Sync tasks were previously commented out in `sync_tasks.py`, which broke imports used by `api/v1/sync.py` and `api/v1/sources.py`. They are implemented again; use a worker configuration that consumes `sync` as described above.

## Resolved: backfill-to-incremental checkpoint bridging

All four connectors (Gmail, Teams, ServiceNow, Jira SM) now seed a checkpoint on the final backfill page so incremental sync can start without manual intervention. Previously, the last page returned `new_checkpoint=None`, breaking the incremental flow.

## Resolved: sync retry dispatch by run_type

`POST /sync/{run_id}/retry` now checks `run.run_type` and dispatches to `run_backfill.delay(...)` for backfill runs or `run_incremental_sync.delay(...)` for incremental runs. Previously, all retries were dispatched as incremental sync regardless of the original run type.

## Resolved: title/body extraction for all connectors

`evidence_title_from_payload` and `evidence_body_from_payload` now cover field names from all connectors (`summary`, `short_description`, `description`, `text`, `snippet`) in addition to the previously handled `title`/`subject`/`body`/`body_text`.

## Resolved: Teams hydrate_thread includes root message

Teams `hydrate_thread` now fetches the root message first via `/messages/{message_id}` before fetching replies, so the parent message body and author are included in the hydrated thread.

## Resolved: dead code removed

- `generate_embeddings` Celery task removed (embeddings are now inline during normalization)
- `discover_source` Celery task removed (discovery runs directly via API and `discover_source_objects`)
- `validate_service_account_token` stub removed from `middleware/auth.py`
- Unused `symptoms` parameter removed from `rank_playbooks`

## Resolved: Correlation now auto-triggers episode reconstruction

`correlate_evidence` (Celery task) now enqueues `reconstruct_episode_task` when new correlation edges are created. Episode reconstruction LLM failures are caught and logged in `create_episodes_from_evidence` so they do not crash the task.

## Resolved: `workspace_id` and `domain_id` now copied from the source at normalization (2026-08-05)

New `EvidenceItem` rows used to land with both NULL — and the graph layer treats a NULL domain as eligible under *every* domain-scoped query, because NULL is the deliberate encoding for reviewed tenant-global knowledge. Unassigned ingest riding that convention meant a domain-limited agent could see evidence nobody had scoped yet (the 2026-08-05 external review's P0-3). Normalization now copies the source's `workspace_id` always, and its domain when unambiguous (source configured with exactly one); a multi-domain source's evidence stays domain-NULL, which genuinely is tenant-wide until a human or correlation narrows it. Reads via `getattr` and degrades to unscoped rather than crashing ingest on an unexpected source shape.

## `body_summary` only via artifact path

Evidence `body_summary` is only populated when attachment artifact extraction runs (via `process_attachment_artifact`). Direct normalization does not generate a summary.

## Semantic search not exposed in evidence API

The evidence list API supports FTS-based search but does not expose the semantic (vector) search path. Semantic search is used internally by the hybrid ranker for playbook ranking.

## Resolved: Evidence chunking foundation (2026-05-08, migration `0030_evidence_chunks`)

Closes the historical "8 KB cliff" where `embed_evidence(title, body[:8000])` made any body content past ~8,000 characters invisible to semantic retrieval. What landed:

- New `evidence_chunks` sibling table (FK to `evidence_items`, `ON DELETE CASCADE`). HNSW index on the chunk embedding column with `m = 16, ef_construction = 64` matching `0021`. GIN `jsonb_path_ops` on `metadata` matching `0025`. Partial B-tree on `evidence_items (tenant_id, ingested_at DESC) WHERE chunked_at IS NULL` to drive the future backfill.
- `services/chunkers/` package with a pure-function `Chunker` Protocol and lazy registry mirroring `connectors/registry.py`. Per-source bodies: `TicketChunker` (Jira / ServiceNow metadata enrichment), `ThreadChunker` (Gmail quote-stripping + author/ts metadata), `AttachmentChunker` (markdown heading split with breadcrumb, JSONL/plain-log boundaries, prose fallback), `FallbackChunker` (recursive paragraph → line → sentence → hard split with overlap).
- `services/evidence_chunk_service.write_chunks` for persistence, with chunk-level `content_hash`, `chunker_version` for re-chunk safety, and connector-defaulted `source_authority` tagging (`runbook` > `ticket` > `email` > `chat` > `gist`).
- Celery tasks in `workers/chunk_tasks.py`: `chunk_evidence_task` (async path for large items, idempotent on `chunker_version`) and `embed_chunks_batch_task` (32-chunk batches via `generate_embeddings_batch`, respecting the per-tenant LLM budget gate from `0023`).
- `_normalize` wiring in `extraction_tasks.py::_dispatch_chunking`: inline chunking for small ticket / thread bodies under 16 KB on the allowlist (`jira_sm`, `servicenow`, `gmail`, `teams`); async dispatch for everything else. Wrapped in `try/except` so a chunker failure cannot regress today's parent-embedding retrieval. Also stamps `EvidenceItem.source_type` from the parent `Source` row when missing — closes the 0029 hole where the column was added but no code path filled it.
- 26 unit tests covering offset accuracy, paragraph / sentence / hard-split fallback, overlap, Gmail quote-stripping (On…wrote, Outlook From/Sent/To, `>` quoted lines, forwarded-only bodies), markdown breadcrumb composition, JSONL log windowing, plain-log timestamp boundaries, registry resolution.
- Detailed design doc at [`CHUNKING_DESIGN.md`](./CHUNKING_DESIGN.md) covering the sibling-table decision, per-source strategy table, `_normalize` integration sketch, search-side rollup plan, backfill, redaction interaction, partition concerns, and explicit "what's not in this PR" list.

**Not yet wired (intentional follow-ups):**

- **Search-side rollup.** `vector_search.py` and `hybrid_ranker.py` still query `evidence_items.embedding` only. Chunks are written but not yet read at query time. The rollup pattern (top-50 chunk hits + MMR + parent grouping with `chunk_id` preservation) is described in `CHUNKING_DESIGN.md §6`.
- **Backfill task.** Existing `EvidenceItem` rows have `chunked_at IS NULL`. A tenant-batched, `ingested_at DESC` drainer needs to land before chunked retrieval can replace parent retrieval as the default path. The partial index `ix_evidence_items_chunked_at_null` is in place to drive it cheaply.
- **Tree-sitter code chunker.** `AttachmentChunker` recognises markdown / JSONL / plain-log / prose; recognised-language code falls through to recursive splitting. Function / class boundary chunking via tree-sitter is a follow-up.
- **Per-tenant authority override.** `_default_authority` maps connector key to source-authority via a fixed table. A `tenant_source_authority` settings table will be needed once a customer says "our wiki *is* authoritative" or "Teams chat for us is the canonical incident log."
- **Per-chunk decision / identity extraction.** `decision_extractor` and `identity_extractor` still run on the parent body once. Running them per-chunk + deduping closes the 4 KB extractor cap in addition to the retrieval cliff.

## `evidence_quality` placeholder in ranker

The hybrid ranker uses a hard-coded `quality_score = 0.5` for all playbooks. A proper evidence-quality signal has not been implemented.

## Open items from the 2026-08-08 review cycle

- ~~**Vertex model-id format unverified**~~ **Resolved by `3f6d3c3`**
  (2026-08-08): model IDs are back to LiteLLM's 2-segment
  `vertex_ai/<model>` form, with region supplied per request via
  `vertex_location`/`vertex_project` kwargs and per-task
  `*_LOCATION` settings (`get_location_for_task`). The
  `supports_reasoning()` matching concern goes away with 2-segment IDs.
- ~~**Pattern/playbook lanes switch models silently on restart**~~
  **Resolved 2026-08-09**: `pattern_model`/`playbook_model` code
  defaults now match what the lanes actually run (and what
  `.env.example` pins): `vertex_ai/gemini-2.5-flash`. Upgrading to
  3.6-flash is a deliberate env change gated on the measure-first A/B.
- **Poison messages on the pattern queue**: `generate_playbook_candidate`
  tasks with malformed UUID args (from 2026-08-07 evening testing) cycle
  on bounded retries in workerB's log. Harmless but noisy; they expire at
  max_retries.

## Open items from the 2026-08-09 review cycle

- ~~**Pattern lane routing is dormant**~~ **Resolved 2026-08-09**:
  `pattern_extractor` now sends `task="pattern"`. Safe to flip because
  the lane defaults were first aligned to the model it already ran
  (2.5-flash) — wiring the lane changed zero behavior; the 3.6 upgrade
  remains a deliberate env + A/B step. `validate_pattern_match` stays
  on `task="verification"` (falls through to the extraction default).
- ~~**`proposed_depends_on` has no approval workflow**~~ **Resolved
  2026-08-09**: `edge_proposal_service` + `/graph/edge-proposals`
  (list/approve/reject, knowledge_manager). Approve mints an authored
  `depends_on` with full review provenance; either verdict closes the
  proposal edge (supersede, never delete). Reviewer UI shipped
  2026-08-09: Graph Explorer → Proposals tab.
- **Ruff backlog (~360 findings)**: pre-existing style violations
  (mostly E501) across older modules. The two genuine runtime bugs
  found in the 2026-08-09 sweep (undefined `logger` in
  `api/v1/patterns.py`, missing `timedelta` import in the Zoho
  connector) are fixed; the style backlog is untouched and should be
  burned down module-by-module, not in one bulk reformat.

## Enterprise-graph blockers (carried from the prior review; four of five resolved 2026-08-09)

- ~~**Inventory CI identity/scoping**~~ **Resolved**: `POST
  /inventory/report` requires `knowledge_manager`; observations may
  carry `external_system`/`external_id` for exact resolution; an
  ambiguous name returns `ambiguous_ci` and writes nothing; unknown
  names are refused (`unknown_ci`) unless the report opts in with
  `create_missing`. Note the behavior change for collectors that
  relied on implicit creation: they must now send
  `create_missing: true`.
- ~~**MAF decision write-back under-provenanced**~~ **Resolved**: the
  payload carries structured `evidence_refs` (every projection-cited
  node) and `approval_required: true`; the in-process client threads
  `session_id`/`domain_id` through to `create_decision`; and the
  projection hides `pending` AI-authored decisions (`hydrators.py`) —
  agent output cannot launder itself into agent input.
- ~~**Outcome/fix flywheel schema-only**~~ **Writers shipped**:
  `case_outcome_service` + session lifecycle hooks. Every session
  open/close appends a `CaseStateTransition`; a close that asserts an
  outcome records `CaseOutcome` (MTTR from the session timeline) and
  links `fix_results` to fix patterns. A close without an outcome
  records the transition only — unstated is unknown, never "resolved".
  Remaining follow-up: aggregation of fix results into
  decision-time statistics (roadmap F10) and richer intermediate
  states once the API exposes them.
- ~~**`asserted_in` vocabulary conflict**~~ **Settled**:
  `asserted_in` = claim→session (materializer); the dormant
  `claim_service` now writes claim→evidence as `supported_by`,
  matching the materializer's support vocabulary, so re-enabling claim
  population cannot interleave two meanings of one edge type.
- **Temporal/execution lineage partial** (still open): `as_of` filters
  edges but hydrated node facts are current-state; playbook versions,
  execution steps, tool invocations, and trace events are not
  projected nodes. Known scope, not a regression.
