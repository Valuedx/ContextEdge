# Episodes, patterns, and playbooks

## Summary

You will see how **episodes** reconstruct what happened from evidence, how **patterns** summarize recurrence across episodes, and how **playbooks** become governed, versioned procedures—with explicit **lifecycle** rules and publication semantics for runtime.

## Business picture

Individual incidents become reusable organizational knowledge through a governed review process. An **episode** captures the full story of one incident—what users reported, what the team tried, what worked, and what the outcome was. When several episodes look alike, the system surfaces a **pattern**: a signal that the same type of problem keeps happening, which helps teams prioritize fixes and documentation. Once a pattern is well understood, it can be promoted into a **playbook**—an approved, versioned procedure that describes exactly how to handle this class of issue. Playbooks go through a formal review cycle (draft → under review → approved) so that only vetted procedures reach the teams and automation that rely on them. At every stage, humans decide what gets promoted; the system organizes and proposes, but people own the final word.

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

## Example: Acme VPN data at this stage

**Stage 1 — Episode (created from AI extraction, pending review)**

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
    { "step_order": 2, "step_type": "diagnostic", "text": "Checked gateway logs — AUTH_CERT_EXPIRED", "evidence_refs": ["ev-d4e5f6"] },
    { "step_order": 3, "step_type": "failed_attempt", "text": "Restarted VPN service — no improvement", "failed_flag": true, "evidence_refs": ["ev-d4e5f6"] },
    { "step_order": 4, "step_type": "remediation", "text": "Renewed gateway certificate via internal CA", "successful_flag": true, "evidence_refs": ["ev-g7h8i9"] },
    { "step_order": 5, "step_type": "outcome", "text": "VPN restored for all affected users", "evidence_refs": ["ev-g7h8i9"] }
  ]
}
```

**Stage 2 — Pattern (clusters similar episodes)**

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

**Stage 3 — Playbook (governed, versioned, approved)**

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

- **Draft episodes with pending review** — *Why:* AI reconstruction is advisory; humans correct narrative before trust. *Tradeoff:* more UI/review workload.

- **Explicit playbook lifecycle vs free text** — *Why:* compliance and runtime safety need known states. *Tradeoff:* more clicks to reach `approved`.

- **Version per semantic version string** — *Why:* customers think in semver; uniqueness per playbook prevents collisions. *Tradeoff:* allocation logic must handle races.

- **Publication timestamp on version** — *Why:* runtime ranks **published** versions only. *Tradeoff:* unpublished drafts invisible to match even if "newer."

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

One **episode** consolidates Acme's duplicate tickets and chat; a **pattern** links it to prior certificate incidents; a **playbook candidate** becomes `under_review`, then `approved` with a **published** version describing certificate rotation—ready for runtime matching described in [05-search-hybrid-and-access.md](./05-search-hybrid-and-access.md).

## Further reading

- [06-ai-extraction-and-embeddings.md](./06-ai-extraction-and-embeddings.md) — how episode text is produced  
- [08-workers-celery-queues.md](./08-workers-celery-queues.md) — `cluster_episodes`, `generate_playbook_candidate`  
- [13-evaluation-drift-and-feedback.md](./13-evaluation-drift-and-feedback.md) — how approved playbooks are monitored for drift and quality  
- [`docs/TECHNICAL_BLUEPRINT.md`](../docs/TECHNICAL_BLUEPRINT.md) — governance section  
