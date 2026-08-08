# Episodes, patterns, and playbooks

## Summary

You will see how **episodes** reconstruct what happened from evidence, how **patterns** summarize recurrence across episodes, and how **playbooks** become governed, versioned proceduresâ€”with explicit **lifecycle** rules and publication semantics for runtime.

## Business picture

Individual incidents become reusable organizational knowledge through a governed review process. An **episode** captures the full story of one incidentâ€”what users reported, what the team tried, what worked, and what the outcome was. When several episodes look alike, the system surfaces a **pattern**: a signal that the same type of problem keeps happening, which helps teams prioritize fixes and documentation. Once a pattern is well understood, it can be promoted into a **playbook**â€”an approved, versioned procedure that describes exactly how to handle this class of issue. Playbooks go through a formal review cycle (draft â†’ under review â†’ approved) so that only vetted procedures reach the teams and automation that rely on them. At every stage, humans decide what gets promoted; the system organizes and proposes, but people own the final word.

## Technical walkthrough

### Episodes

- `episode_service.create_episodes_from_evidence` calls `reconstruct_episode` (AI), merges identity refs from underlying `EvidenceItem` rows, and inserts `Episode` plus `EpisodeStep` children with `status="draft"` and `reviewer_state="pending_review"`.
- Each new episode also resolves a **`primary_case_ref`** â€” the ticket number it should be cited by (`_resolve_primary_case_ref`). The rule: follow the episode's *own* cited evidence to its canonical cases via `case_links`, prefer the case most of that evidence points at, then take the identifier the correlation layer already marked `is_authoritative`. Returns `None` rather than a guess when evidence has no linked case (normal for `local_file` ingests, where the ticket number exists only in a filename â€” a filename is not a verified identifier). Without this field an agent can name an episode but never the record behind it, so a claim cannot be checked in one click.
- API routes in `api/v1/episodes.py` expose list/get/create flows for tenant-scoped use (details per `docs/API.md`).

### Patterns

- `pattern_service.create_pattern_from_episodes` creates a `Pattern` with metadata fields (triggers, entities, errors, root causes, resolution steps), links episodes through `PatternEvidenceLink`, and calls `promote_pattern_memory` in `memory_service` to reinforce long-lived graph/memory associations.

### Playbooks

- `playbook_service.py` implements a **state machine** (`VALID_TRANSITIONS`): e.g. `candidate` â†’ `under_review` â†’ `approved`, plus restricted/deprecated/expired/retired paths.
- `transition_playbook` validates transitions; approving sets `published_at` / `published_by` on the **current** `PlaybookVersion` and records approvals.
- Semantic versions use `_next_semantic_version` with retry on unique conflicts (`IntegrityError`) to handle concurrency.
- `promote_playbook_memory` ties approved knowledge into memory/graph promotion when called from transitions.
- `api/v1/playbooks.py` exposes CRUD and lifecycle operations aligned with service rules.
- Per-step metadata on `PlaybookVersion.steps` is validated on write through the `PlaybookStep` Pydantic schema (`schemas/playbook.py`) â€” each step carries `reversible`, `time_estimate_sec`, `verification`, `rollback_hint`, `safety_class`, and `tool_ref`. All fields optional with defaults so pre-M2 payloads keep validating, and `extra="allow"` preserves vendor-specific keys. Storage is unchanged (JSONB list).
- `PlaybookVersion.verification_policy` (JSONB) declares post-action recheck behavior â€” `auto_close_on_success`, `recheck_after_sec`, `recheck_metric`, `recheck_source` â€” so the reviewer console's "auto-close on successful recheck" commitment renders from data, not copy. Fields are descriptive at this revision; the recheck worker that honours them is a follow-up (see [KNOWN_GAPS.md](./KNOWN_GAPS.md)).

### Playbook generation quality

Reviewing an early generated corpus (37 playbooks, 223 steps) against its sources showed it splitting three ways: genuinely good episode-grounded procedures, structured-but-hollow filler on patterns the model itself scored 0.1â€“0.4, and "playbooks" for things that are not remediations. Four controls now shape what gets generated and reviewed:

- **Evidence floor** (`pattern_tasks.py`, `PLAYBOOK_GENERATION_MIN_PATTERN_CONFIDENCE = 0.5`): generation is skipped â€” before any LLM spend, with a logged reason â€” for patterns below the floor. `pattern_type` cannot gate this (everything is `recurring_issue`), so confidence is the signal. Skips are not auto-retried; the manual `POST /playbooks/generate` route bypasses the worker gate for a human who disagrees.
- **Knowledge threshold** (`knowledge_retrieval_service.MAX_DISTANCE`, derived from `KNOWLEDGE_LINK_MIN_SIMILARITY = 0.75`): what is too weak to assert as a graph edge is also too weak to become a step a reviewer is asked to approve. Measured on a live tenant: genuine patternâ†”document pairs sit at similarity 0.75â€“0.84, vocabulary noise at 0.62â€“0.69 â€” the bands do not overlap.
- **Prompt v4** (`ai/prompts/playbook.py`; earlier versions stay registered and immutable for eval baselines): reproduce a source's literal command *exactly* rather than paraphrasing it into "use an SSL testing tool"; `kb-N`/`ep-N` labels belong in `source_refs` and nowhere in prose (persisted text saying "as per KB-1" resolves to nothing); an unsourced step must state in `expected_outcome` what observable result would confirm it.
- **Grounded vs best-practice taxonomy** (prompt **v5**, default since 2026-08-08): every step is either *grounded* (explicitly supported by a supplied source, citing it in `source_refs`) or *best-practice* (an expert-expected operational step no source states â€” backup prep, health checks, customer comms â€” tagged `grounding_status: non_grounded`, `step_classification: best_practice`, with a fixed reason string). The tags are **enforced structurally** in `generators/playbook_generator.classify_step_grounding` after citation cleaning, so a step whose minted citations were dropped is downgraded no matter what the model claimed, and an evidenced step cannot be mislabeled to dodge review. Best-practice steps can only lower the `grounded_ratio`, never raise it. The UI badges them ("Best Practice (Non-Grounded)", filterable) and the maf.v1 projection prefixes `[best practice]` on their labels â€” neither humans nor agents may mistake expert inference for sourced procedure. Live-validated: 10 steps â†’ 3 grounded / 7 tagged best-practice / 0 untagged.
- **Preventive dedup, scoped to identity not labels** (2026-08-08): episode creation merges into an existing episode only when it is *active* and **shares evidence** (same occurrence) â€” a bare title match would fuse different incidents that share a label, the cross-case contamination the recurrence system exists to avoid. Pattern creation merges by title only within the *same domain* among active patterns. The batch `deduplicate_*` sweeps supersede (never hard-delete), skip settled rows, and split same-title episode groups into evidence-overlap components before merging.
- **Review-queue ordering** (`api/v1/playbooks.py`): listing with `lifecycle_state=candidate` orders by the generator's own confidence, not recency. Approval is what makes a playbook agent-visible, and with 30+ candidates pending, recency buried the fully-sourced 0.9-confidence ones beneath whatever generated last.

One projection gotcha, fixed but worth knowing: seeded playbooks store steps as `{"order", "instruction"}` while generated ones use `{"text", ...}`. The graph hydrator and the embedding text both read `title`/`text`/`action`/`instruction` â€” before `instruction` was added to that chain, every *approved* playbook (the only kind an agent may see) projected an empty step list and embedded on its title alone.

### Relationships

- Episodes ground patterns in real evidence; patterns can feed **playbook candidate** generation (see pattern workers). Playbooks link **evidence** via `PlaybookEvidenceLink` at version scope for semantic retrieval.
- Knowledge retrieval at generation time also **persists** what it finds: confident, applicability-clean patternâ†’document matches are written as `pattern -supported_by-> evidence` graph edges (`persist_knowledge_links`), so a document that informed a playbook stays reachable by graph traversal afterwards instead of being spent on one prompt.

## Example: Acme VPN data at this stage

**Stage 1 â€” Episode (created from AI extraction, pending review)**

```json
{
  "episode_id": "ep-x1y2z3",
  "tenant_id": "acme-corp",
  "domain_id": "vpn-connectivity",
  "title": "Corporate VPN authentication failure after KB5032190",
  "status": "draft",
  "reviewer_state": "pending_review",
  "extraction_confidence": 0.87,
  "root_cause_summary": "Windows update KB5032190 invalidated the gateway certificate chain",
  "final_outcome": "Certificate renewed via internal CA; VPN restored",
  "steps": [
    { "step_order": 1, "step_type": "complaint", "text": "Users report VPN drops post-patch Tuesday", "evidence_refs": ["ev-a1b2c3"] },
    { "step_order": 2, "step_type": "diagnostic", "text": "Checked gateway logs â€” AUTH_CERT_EXPIRED", "evidence_refs": ["ev-d4e5f6"] },
    { "step_order": 3, "step_type": "failed_attempt", "text": "Restarted VPN service â€” no improvement", "failed_flag": true, "evidence_refs": ["ev-d4e5f6"] },
    { "step_order": 4, "step_type": "remediation", "text": "Renewed gateway certificate via internal CA", "successful_flag": true, "evidence_refs": ["ev-g7h8i9"] },
    { "step_order": 5, "step_type": "outcome", "text": "VPN restored for all affected users", "evidence_refs": ["ev-g7h8i9"] }
  ]
}
```

**Stage 2 â€” Pattern (clusters similar episodes)**

```json
{
  "pattern_id": "pat-m1n2o3",
  "tenant_id": "acme-corp",
  "domain_id": "vpn-connectivity",
  "title": "Certificate expiry after Windows cumulative updates",
  "pattern_type": "recurring_issue",
  "confidence": 0.82,
  "episode_count": 7,
  "triggers": ["Windows cumulative update", "certificate chain validation change"],
  "common_root_cause": "Cumulative updates occasionally invalidate certificate chains trusted by network appliances",
  "common_resolution": "Renew affected certificates via internal CA before or immediately after patch rollout"
}
```

**Stage 3 â€” Playbook (governed, versioned, approved)**

```json
{
  "playbook_id": "pb-r1s2t3",
  "title": "VPN Certificate Rotation After Patch Tuesday",
  "lifecycle_state": "approved",
  "risk_tier": "medium",
  "automation_mode": "human_confirmed",
  "current_version": {
    "version_id": "ver-001",
    "semantic_version": "1.0.0",
    "trigger_conditions": "VPN auth failures with AUTH_CERT_EXPIRED after a Windows cumulative update",
    "published_at": "2026-03-20T14:00:00Z",
    "published_by": "reviewer@acme.com",
    "evidence_refs": ["ev-a1b2c3", "ev-d4e5f6", "ev-g7h8i9"]
  }
}
```

The playbook is only visible to runtime retrieval after it reaches `approved` state and has a published version. Draft and under-review versions are invisible to downstream consumers.

## Design decisions

- **Draft episodes with pending review** â€” *Why:* AI reconstruction is advisory; humans correct narrative before trust. *Tradeoff:* more UI/review workload.

- **Explicit playbook lifecycle vs free text** â€” *Why:* compliance and runtime safety need known states. *Tradeoff:* more clicks to reach `approved`.

- **Version per semantic version string** â€” *Why:* customers think in semver; uniqueness per playbook prevents collisions. *Tradeoff:* allocation logic must handle races.

- **Publication timestamp on version** â€” *Why:* runtime ranks **published** versions only. *Tradeoff:* unpublished drafts invisible to match even if "newer."

- **Confidence floor on generation, not on review** â€” *Why:* a hollow candidate costs reviewer attention and dilutes trust in the good ones; skipping before the LLM call also costs nothing. *Tradeoff:* a pattern that accrues evidence later needs the manual generate route â€” nothing re-dispatches automatically.

- **Two thresholds for knowledge, deliberately different (0.6 seed vs 0.75 edge/step)** â€” *Why:* a weak *seed* ranks low and falls out of the projection budget; a weak *edge or step* is asserted as fact and read back forever. Wrong seeds cost a little context; wrong edges corrupt the graph. *Tradeoff:* thin coverage until product-derived patterns accumulate â€” 3 documents linked on the measured corpus, and that was the correct answer.

## Code map

| Concern | Module path | Key symbols | When it runs |
| --- | --- | --- | --- |
| Episodes | `backend/src/contextedge/services/episode_service.py` | `create_episodes_from_evidence` | API / extraction task |
| Episode model | `backend/src/contextedge/models/episode.py` | `Episode`, `EpisodeStep` | ORM |
| Episode API | `backend/src/contextedge/api/v1/episodes.py` | (router handlers) | HTTP |
| Patterns | `backend/src/contextedge/services/pattern_service.py` | `create_pattern_from_episodes` | API / workers |
| Pattern model | `backend/src/contextedge/models/pattern.py` | `Pattern`, `PatternEvidenceLink`, `GraphEdge` | ORM |
| Pattern API | `backend/src/contextedge/api/v1/patterns.py` | (router handlers) | HTTP |
| Playbook governance | `backend/src/contextedge/services/playbook_service.py` | `transition_playbook`, `VALID_TRANSITIONS`, `_next_semantic_version` | Approvals |
| Playbook model | `backend/src/contextedge/models/playbook.py` | `Playbook`, `PlaybookVersion`, `PlaybookApproval` | ORM |
| Playbook API | `backend/src/contextedge/api/v1/playbooks.py` | (router handlers) | HTTP |
| Memory promotion | `backend/src/contextedge/services/memory_service.py` | `promote_pattern_memory`, `promote_playbook_memory` | Transitions / pattern create |
| Generation gate | `backend/src/contextedge/workers/pattern_tasks.py` | `PLAYBOOK_GENERATION_MIN_PATTERN_CONFIDENCE`, `generate_playbook_candidate` | Celery, pattern create |
| Knowledge for generation | `backend/src/contextedge/services/knowledge_retrieval_service.py` | `retrieve_knowledge_for_pattern`, `persist_knowledge_links`, `MAX_DISTANCE`, `KNOWLEDGE_LINK_MIN_SIMILARITY` | Generation |
| Generator prompt | `backend/src/contextedge/ai/prompts/playbook.py` | `v5` (default), earlier versions immutable | Generation |
| Case reference | `backend/src/contextedge/services/episode_service.py` | `_resolve_primary_case_ref` | Episode create |

## Acme VPN incident (this layer)

One **episode** consolidates Acme's duplicate tickets and chat; a **pattern** links it to prior certificate incidents; a **playbook candidate** becomes `under_review`, then `approved` with a **published** version describing certificate rotationâ€”ready for runtime matching described in [05-search-hybrid-and-access.md](./05-search-hybrid-and-access.md).

## Further reading

- [06-ai-extraction-and-embeddings.md](./06-ai-extraction-and-embeddings.md) â€” how episode text is produced  
- [08-workers-celery-queues.md](./08-workers-celery-queues.md) â€” `cluster_episodes`, `generate_playbook_candidate`  
- [13-evaluation-drift-and-feedback.md](./13-evaluation-drift-and-feedback.md) â€” how approved playbooks are monitored for drift and quality  
- [`docs/TECHNICAL_BLUEPRINT.md`](../docs/TECHNICAL_BLUEPRINT.md) â€” governance section  
