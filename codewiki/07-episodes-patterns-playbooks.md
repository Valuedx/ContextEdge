# Episodes, patterns, and playbooks

## Summary

You will see how **episodes** reconstruct what happened from evidence, how **patterns** summarize recurrence across episodes, and how **playbooks** become governed, versioned procedures—with explicit **lifecycle** rules and publication semantics for runtime.

## Business picture

An **episode** is the story of one incident: symptoms, steps tried, outcome. A **pattern** is “this keeps happening” across several episodes—useful for prioritizing engineering or documentation work. A **playbook** is the official “how we handle it” artifact: steps, risk, automation mode, and versions that can be **published** so runtime and execution only use blessed content.

## Technical walkthrough

### Episodes

- `episode_service.create_episodes_from_evidence` calls `reconstruct_episode` (AI), merges identity refs from underlying `EvidenceItem` rows, and inserts `Episode` plus `EpisodeStep` children with `status="draft"` and `reviewer_state="pending_review"`.
- API routes in `api/v1/episodes.py` expose list/get/create flows for tenant-scoped use (details per `docs/API.md`).

### Patterns

- `pattern_service.create_pattern_from_episodes` creates a `Pattern` with metadata fields (triggers, entities, errors, root causes, resolution steps), links episodes through `PatternEvidenceLink`, and calls `promote_pattern_memory` in `memory_service` to reinforce long-lived graph/memory associations.

### Playbooks

- `playbook_service.py` implements a **state machine** (`VALID_TRANSITIONS`): e.g. `candidate` → `under_review` → `approved`, plus restricted/deprecated/expired/retired paths.
- `transition_playbook` validates transitions; approving sets `published_at` / `published_by` on the **current** `PlaybookVersion` and records approvals.
- Semantic versions use `_next_semantic_version` with retry on unique conflicts (`IntegrityError`) to handle concurrency.
- `promote_playbook_memory` ties approved knowledge into memory/graph promotion when called from transitions.
- `api/v1/playbooks.py` exposes CRUD and lifecycle operations aligned with service rules.

### Relationships

- Episodes ground patterns in real evidence; patterns can feed **playbook candidate** generation (see pattern workers). Playbooks link **evidence** via `PlaybookEvidenceLink` at version scope for semantic retrieval.

## Design decisions

- **Draft episodes with pending review** — *Why:* AI reconstruction is advisory; humans correct narrative before trust. *Tradeoff:* more UI/review workload.

- **Explicit playbook lifecycle vs free text** — *Why:* compliance and runtime safety need known states. *Tradeoff:* more clicks to reach `approved`.

- **Version per semantic version string** — *Why:* customers think in semver; uniqueness per playbook prevents collisions. *Tradeoff:* allocation logic must handle races.

- **Publication timestamp on version** — *Why:* runtime ranks **published** versions only. *Tradeoff:* unpublished drafts invisible to match even if “newer.”

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

## Acme VPN incident (this layer)

One **episode** consolidates Acme’s duplicate tickets and chat; a **pattern** links it to prior certificate incidents; a **playbook candidate** becomes `under_review`, then `approved` with a **published** version describing certificate rotation—ready for runtime matching described in [05-search-hybrid-and-access.md](./05-search-hybrid-and-access.md).

## Further reading

- [06-ai-extraction-and-embeddings.md](./06-ai-extraction-and-embeddings.md) — how episode text is produced  
- [08-workers-celery-queues.md](./08-workers-celery-queues.md) — `cluster_episodes`, `generate_playbook_candidate`  
- [`docs/TECHNICAL_BLUEPRINT.md`](../docs/TECHNICAL_BLUEPRINT.md) — governance section  
