# Playbook Editing — End-to-End Implementation Plan

**Scope:** make the Playbook UI fully editable — edit existing content, add new steps, add
detail to existing steps, reorder steps, and save — across `frontend` and `backend`.

**Target repo:** `D:\ContextEdge_pro\ContextEdge`
**Screens:** `/playbooks`, `/playbooks/{id}`
**Prime directive:** additive only. Nothing in the current read, review, approve, runtime-match,
embedding, or agent-projection paths changes behaviour.

---

## 1. What exists today (review findings)

### 1.1 Data model — `backend/src/contextedge/models/playbook.py`

| Table | Role | Notes |
|---|---|---|
| `playbooks` | the governed object | `title`, `description`, `lifecycle_state`, `risk_tier`, `automation_mode`, `approval_policy_id`, `current_version_id`, `embedding`, `lexical_search_text`, generated `search_tsvector` |
| `playbook_versions` | immutable-ish content snapshot | `semantic_version`, `trigger_conditions` (JSONB), `branching_logic`, `inputs`, `outputs`, **`steps` (JSONB array)**, `rollback_notes`, `evidence_refs`, `conflicts`, `playbook_confidence`, `execution_confidence_guidance`, `verification_policy`, `generation_provenance`, `published_at`, `published_by`, `created_at` |
| `playbook_evidence_links` | normalised provenance | written by `_materialize_evidence_links` at version create |
| `playbook_approvals` | one row per lifecycle transition | |
| `playbook_negative_knowledge` | NK links | |

Key observations:

- **`playbook_versions` has `created_at` but no `updated_at`, no revision counter, no
  `created_by`/`last_edited_by`, and no fork lineage column.** There is nothing today to
  build optimistic locking or "who edited this" on.
- `UniqueConstraint(playbook_id, semantic_version)` — version numbers are unique per playbook.
- **Steps carry no stable identity.** They are anonymous dicts in a JSONB array.

### 1.2 The step shape is a *union*, and that is the biggest risk in this feature

`schemas/playbook.PlaybookStep` declares `index / title / description / action_name /
action_type / safety_class / requires_approval / reversible / time_estimate_sec /
verification / rollback_hint / tool_ref / inputs / outputs_schema` — with
`model_config = ConfigDict(extra="allow")`.

The **stored** steps (generator output) actually use a different vocabulary, which is what the
UI renders (`components/common/playbook-steps.tsx`):
`text / type / order / status / on_failure / expected_outcome / evidence_quality /
source_refs[] / grounding_status / step_classification / reason`.

Those survive today *only* because `extra="allow"` lets them through un-validated. Additional
keys seen in the wild: `action`, `instruction` (seeded playbooks — see
`playbook_embedding.build_playbook_embedding_text`).

> **Consequence:** any editor that round-trips a step as a typed object will silently delete
> `source_refs`, `grounding_status`, `evidence_quality`, `action_name`, `inputs`,
> `outputs_schema` — i.e. it will strip the provenance the reviewer console exists to show.
> The plan below therefore mandates **merge-by-key, never replace-the-object**.

### 1.3 Write paths that exist

| Endpoint | Role gate | Behaviour |
|---|---|---|
| `POST /playbooks` | `knowledge_manager` | creates shell, no version |
| `PATCH /playbooks/{id}` | `knowledge_manager` (+`tenant_admin` for `automation_mode` / `approval_policy_id`) | updates `title`, `description`, `risk_tier`, `automation_mode`, `approval_policy_id`, `reviewer_user_id`; re-embeds when title/description change |
| `POST /playbooks/{id}/versions` | `knowledge_manager` | `create_playbook_version()` → new row, auto patch-bump, repoints `current_version_id`, materialises evidence links, emits `playbook.version_created` |
| `POST /playbooks/{id}/transition` | `playbook_reviewer` | state machine + publish + memory promotion + embed + Redis cache flush |
| `POST /playbooks/bulk-transition` | `playbook_reviewer` | |
| `POST /playbooks/{id}/rollback` | `knowledge_manager` | copies an old version forward as a new version |
| `GET .../versions/{vid}/diff` | any | `difflib` over `_version_payload()` |
| `POST /playbooks/generate` | `knowledge_manager` | AI generation |

There is **no in-place update of a version's content anywhere**. That is the gap.

### 1.4 Invariants the feature must not break

1. **Published ≠ current.** `create_playbook_version` repoints `current_version_id` immediately,
   *before* review. Every runtime consumer therefore resolves the **newest published** version:
   - `services/playbook_embedding.resolve_published_playbook_version`
   - `api/v1/runtime.py::_resolve_runtime_published_version` and `GET /runtime/playbooks/{key}`
     (this is what SupportCopilot reads)
   - `graph/agent/hydrators.playbook_version_facts` + repository fallback
     (`test_playbook_steps_projection.py::test_unpublished_current_falls_back_to_newest_published`)
   **A draft must never leak into runtime.** Editing must produce drafts, not mutate published rows.
2. **A version with zero steps cannot enter `under_review` or `approved`**
   (`transition_playbook`).
3. **Approved playbooks keep the published fingerprint.** `create_version` deliberately skips
   `embed_playbook` when `lifecycle_state == "approved"` (N3).
4. `validate_step_bindings` runs before any version row is created: a step naming a `tool_ref`
   must resolve in the skill registry.
5. `allowed_transitions` is served by the API; the UI must not re-derive it.
6. Steps render **by declared `order`**, not array position (`sortSteps`) — the two disagree in
   stored data.

### 1.5 Bugs / rough edges found while reviewing (fix inside this feature)

| # | Finding | Fix |
|---|---|---|
| B1 | `create_version` does not catch `UnresolvedSkillReference` → an unknown `tool_ref` returns **500**, not 422. | Catch in both create and the new patch endpoint → 422. |
| B2 | `TransitionDialog` in `playbooks/[id]/page.tsx` posts `comment` but the API expects `comments` — the review note is silently dropped. (`PlaybookLifecycleActions` gets it right.) | Fix to `comments`, or delete the now-unused `TransitionDialog`. |
| B3 | `_version_payload()` (diff + rollback source) omits `verification_policy` and `generation_provenance`, so edits to verification policy are invisible in the diff **and are dropped by rollback**. | Add `verification_policy` to `_version_payload`. Leave `generation_provenance` out of rollback (a rolled-back copy was not model-generated). |
| B4 | TS `PlaybookVersion` lacks `verification_policy` though the API returns it. | Add to `lib/types`. |
| B5 | `PlaybookVersion.updated_at` does not exist, and `Playbook.updated_at` only moves when the playbook row itself is written — a version edit would leave the list's "Updated" column stale. | New column + explicit touch of the parent row. |

---

## 2. Design decisions

### D1 — Draft-mutable, published-immutable (chosen)

| Option | Verdict |
|---|---|
| A. Every save creates a new `playbook_versions` row | Rejected — version explosion (one row per Save), and `semantic_version` loses meaning. |
| **B. A version is editable in place while `published_at IS NULL`; a published version is immutable and "Edit" forks a new draft (copy-on-write)** | **Chosen.** Preserves every invariant in §1.4 for free: runtime/embedding/projection all read published rows, which never change. |
| C. Separate `playbook_drafts` table | Rejected — duplicates the whole content schema and every consumer's resolution logic. |

**Rule:** *edits change a draft's `revision`; forking or authoring bumps `semantic_version`;
publishing is still and only the lifecycle transition.*

### D2 — Steps get a stable `step_id`

Reordering, per-step diffs, per-step approval traces and React keys all need identity.
Add `step_id` (uuid4 string) **inside the step JSON**, assigned by the backend normaliser on
first save of a version. **Do not** backfill historical published versions with a data
migration — they are immutable and their diffs would all churn. Normalise lazily on write.

### D3 — Merge, never replace

The wire format for a step edit is a **partial dict**. Server-side merge:
`stored_step | patch` for known keys, unknown stored keys untouched, and an explicit
`__remove__` sentinel list for the rare "clear this field". A `PUT`-style whole-array replace is
still accepted for reorder, but each element is matched by `step_id` back to the stored step and
merged into it — never substituted for it.

### D4 — Human edits are labelled, not laundered

A hand-authored step must not inherit the appearance of evidence-grounded content.

- New step defaults: `grounding_status: "non_grounded"`, `step_classification: "human_authored"`,
  `source_refs: []`, `evidence_quality` absent.
- Editing the instruction text of a `grounded` step sets `human_edited: true`,
  `edited_by`, `edited_at` on that step and leaves `source_refs` intact — the citation still
  says where the step came from; the badge says a human has since changed the wording.
- `playbook-steps.tsx` gains one badge ("Edited") for `human_edited`. No other render change.

### D5 — Optimistic concurrency, not last-write-wins

`PATCH` requires `expected_revision`. Mismatch → **409** with the current revision and
`updated_at` so the client can offer "reload / keep mine".

---

## 3. Database changes

**Migration `0093_playbook_version_editing`** (`down_revision = "0092_copilot_audit"`; follow the
`SELECT set_config('app.bypass_rls','on',false)` preamble used by 0092).

```sql
ALTER TABLE playbook_versions
  ADD COLUMN updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  ADD COLUMN revision     INTEGER     NOT NULL DEFAULT 1,
  ADD COLUMN created_by   UUID        NULL,
  ADD COLUMN last_edited_by UUID      NULL,
  ADD COLUMN derived_from_version_id UUID NULL
      REFERENCES playbook_versions(id) ON DELETE SET NULL;

UPDATE playbook_versions SET updated_at = created_at;

-- cheap lookup for "the open draft of this playbook"
CREATE INDEX ix_playbook_versions_open_draft
  ON playbook_versions (playbook_id)
  WHERE published_at IS NULL;
```

Downgrade drops the index and the five columns.

**Model changes** (`models/playbook.py`, `PlaybookVersion`):

```python
updated_at: Mapped[datetime] = mapped_column(
    DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
revision: Mapped[int] = mapped_column(Integer, server_default="1", nullable=False)
created_by: Mapped[uuid.UUID | None]
last_edited_by: Mapped[uuid.UUID | None]
derived_from_version_id: Mapped[uuid.UUID | None] = mapped_column(
    UUID(as_uuid=True), ForeignKey("playbook_versions.id", ondelete="SET NULL"), nullable=True)
```

No change to the `steps` column type. `step_id` and the edit-provenance keys live inside the
JSONB, which is exactly what `extra="allow"` already tolerates.

**Not adding a `playbook_version_edits` table.** `audit_logs` (`log_audit_event`) and
`operational_events` (`append_operational_event`) already carry the audit story; a third
lineage store is a maintenance cost with no reader.

---

## 4. Backend implementation

### 4.1 New/changed schemas — `schemas/playbook.py`

```python
class PlaybookStepPatch(BaseModel):
    """One step as sent by the editor. Partial by construction."""
    model_config = ConfigDict(extra="allow")

    step_id: str | None = None          # absent ⇒ new step, server assigns
    # editable surface (all optional, all merged)
    text: str | None = None
    title: str | None = None
    description: str | None = None
    type: str | None = None             # diagnostic | remediation | verification | escalation | communication
    expected_outcome: str | None = None
    on_failure: str | None = None
    reason: str | None = None
    rollback_hint: str | None = None
    safety_class: str | None = None     # validated against SAFETY_CLASSES
    action_type: str | None = None      # validated against ACTION_TYPES
    action_name: str | None = None
    tool_ref: str | None = None
    requires_approval: bool | None = None
    reversible: bool | None = None
    verification: bool | None = None
    time_estimate_sec: int | None = Field(default=None, ge=0, le=86_400)
    clear_fields: list[str] = Field(default_factory=list)   # explicit unset
    # `order` is NOT accepted — array position is the single source of order.

class PlaybookVersionUpdate(BaseModel):
    expected_revision: int = Field(..., ge=1)
    steps: list[PlaybookStepPatch] | None = None    # full ordered array when present
    trigger_conditions: dict | None = None
    branching_logic: dict | None = None
    inputs: list | None = None
    outputs: list | None = None
    rollback_notes: str | None = None
    execution_confidence_guidance: str | None = None
    verification_policy: VerificationPolicy | None = None
    playbook_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    edit_note: str | None = Field(default=None, max_length=500)   # goes to the audit row

class PlaybookVersionForkRequest(BaseModel):
    edit_note: str | None = None
```

`PlaybookVersionResponse` gains `revision: int`, `updated_at: datetime`,
`derived_from_version_id: UUID | None`, `created_by`/`last_edited_by`, and
`is_editable: bool` (computed: `published_at is None`).

**Reorder is expressed by array position.** Sending the full `steps` array in the desired order
is the reorder operation; the server rewrites each step's `order` to `1..n` so the stored
`order` field and array position can never disagree again (§1.4.6).

### 4.2 New service module — `services/playbook_editing.py`

```
normalize_steps(existing: list, patches: list[PlaybookStepPatch], actor_id) -> list[dict]
```

Algorithm:

1. Index `existing` by `step_id` (steps without one are matched positionally on first pass and
   assigned an id).
2. For each patch, in the order given:
   - existing step found → `merged = dict(stored)`, apply set fields, drop `clear_fields`,
     preserve every untouched key.
   - no `step_id` → new step: `{step_id: uuid4, grounding_status: "non_grounded",
     step_classification: "human_authored", source_refs: [], created_by, created_at}` + patch.
   - if the instruction text changed and `grounding_status == "grounded"` → set
     `human_edited/edited_by/edited_at` (D4).
3. Steps in `existing` whose `step_id` is absent from the patch list are **deleted** (this is how
   the UI removes a step) — the count of deletions is returned for the audit payload.
4. Rewrite `order = i + 1` and `index = i` for every step.
5. Return `(steps, summary)` where summary = `{added: [ids], removed: [ids], modified: [ids],
   reordered: bool}`.

```
validate_steps(steps) -> None      # raises PlaybookEditValidationError
```

- ≥1 step required to *save*? **No** — an empty draft is savable (the transition guard already
  blocks review/approve). Saving zero steps returns a warning field, not an error.
- every step needs non-empty instruction (`text` or `title` or `description`), ≤ 4 000 chars
- `type` ∈ known set (warn-only if not; do not reject vendor values)
- `safety_class` ∈ `SAFETY_CLASSES`, `action_type` ∈ `ACTION_TYPES` (hard 422)
- `time_estimate_sec` 0…86 400
- ≤ **100** steps per version; total serialised `steps` payload ≤ **512 KB**
  (protects JSONB size, the 4 000-char embedding budget and `PLAYBOOK_STEPS_CAP`)
- `trigger_conditions` ≤ 32 KB, `rollback_notes` ≤ 8 KB
- duplicate `step_id` → 422

### 4.3 New endpoints — `api/v1/playbooks.py`

#### `PATCH /playbooks/{playbook_id}/versions/{version_id}` → `PlaybookVersionResponse`

```
user.require_role("knowledge_manager")
load playbook (tenant-scoped)          -> 404
load version (must belong to playbook) -> 404
if playbook.lifecycle_state in {"retired", "deprecated"}: 409
if version.published_at is not None:   409  {code: "version_published",
                                             hint: "fork a draft", fork_url: ...}
if version.id != playbook.current_version_id: 409 {code: "not_current_version"}
if body.expected_revision != version.revision: 409 {code: "revision_conflict",
                                                    current_revision, updated_at}
steps, summary = normalize_steps(version.steps, body.steps, user.user_id)   # when present
validate_steps(steps)
try: await validate_step_bindings(db, tenant_id, steps)
except UnresolvedSkillReference as e: 422            # ← fixes B1
apply changed fields; version.revision += 1; version.last_edited_by = user.user_id
playbook.updated_at = func.now()                     # ← fixes B5
if steps or trigger_conditions changed and playbook.lifecycle_state != "approved":
    await embed_playbook(db, playbook, version)      # honours N3
await append_operational_event(... "playbook.version_edited", payload={revision, summary, ...})
await log_audit_event(... action="playbook.version_edited", resource_type="playbook_version",
                      resource_id=str(version.id),
                      details={playbook_id, semantic_version, changed_fields, summary, edit_note})
```

Audit payload carries **field names and step ids only** — never full step text — so the row
stays small and no content is duplicated into the audit store.

#### `POST /playbooks/{playbook_id}/versions/{version_id}/draft` → `PlaybookVersionResponse` (201)

Copy-on-write fork of a published version.

```
user.require_role("knowledge_manager")
if an unpublished draft already exists for this playbook: return it (200, idempotent)
data = _version_payload(target) + verification_policy      # ← B3
version = await create_playbook_version(db, playbook, data)   # reuses the existing helper
version.derived_from_version_id = target.id
version.created_by = user.user_id
# create_playbook_version already repoints current_version_id and materialises evidence links.
# lifecycle_state == "approved" ⇒ embed is skipped, published fingerprint preserved (N3)
audit "playbook.version_forked"
```

Because `create_playbook_version` re-runs `_materialize_evidence_links`, the fork keeps its
provenance rows. The published version is untouched, so `/runtime/playbooks/{key}`,
`rank_playbooks`, the agent projection and the embedding all continue to serve the approved
content until the draft is itself approved.

#### `DELETE /playbooks/{playbook_id}/versions/{version_id}` → 204 (optional, phase 6)

Discard an unpublished draft. Guards: never published, never the only version; repoints
`current_version_id` back to the newest published version (or the previous draft). Emits
`playbook.version_discarded`. **Ship only if the "Discard draft" button is in scope** —
otherwise abandoned drafts are harmless.

#### Changed: `POST /playbooks/{id}/versions`

Add the `UnresolvedSkillReference → 422` catch (B1). Set `created_by`. No other change.

#### Changed: `PATCH /playbooks/{id}`

No signature change. `PlaybookUpdate` already covers `title`, `description`, `risk_tier` — the UI
simply has no control for them today. Add an audit event (`playbook.updated`) here, which is
currently missing while `playbook.created` exists.

### 4.4 Transaction & error semantics

`get_db()` commits at the end of a successful request, rolls back on exception — so each endpoint
is one transaction. Keep it that way: no explicit `commit()` in the new endpoints (unlike
`generate_playbook`, which commits because it wraps a broad `except`).

| Status | Cause | Body |
|---|---|---|
| 400 | malformed uuid / bad lifecycle target | `detail: str` |
| 401 | no/expired token (client redirects to `/login`) | |
| 403 | missing `knowledge_manager` | `Role 'knowledge_manager' required` |
| 404 | playbook or version not found, or other tenant | `detail: str` |
| **409** | published version, non-current version, revision conflict, read-only lifecycle | `detail: {code, message, current_revision?, updated_at?}` |
| **422** | pydantic validation, step validation, unresolved `tool_ref`, policy ceiling | pydantic array or `detail: str` |
| 500 | unexpected — `logger.exception` with tenant/playbook/version ids, generic message to client | |

`lib/api.ts` already flattens pydantic error arrays into a readable message; a dict `detail` is
JSON-stringified, so **return `detail` as a string for anything the user must read**, and put the
machine-readable code in a sibling header or keep the dict shape and teach `api.ts` to read
`detail.message` (preferred — one small change in `api.ts`).

---

## 5. Frontend implementation

### 5.1 New shared module — `frontend/src/lib/playbook-steps.ts`

Pure, unit-testable, no React:

```ts
export type EditableStep = Record<string, unknown> & { step_id: string };
ensureStepIds(steps: unknown): EditableStep[]        // client-side temp ids for new rows
stepInstruction(step): string                        // text ?? title ?? action ?? instruction
mergeStepEdit(step, patch): EditableStep             // never drops unknown keys
moveStep(steps, from, to): EditableStep[]
insertStepAfter(steps, index): EditableStep[]
duplicateStep(steps, index): EditableStep[]
removeStep(steps, id): EditableStep[]
toPatchPayload(original, edited): PlaybookStepPatch[] // diff-aware; sends only changed keys
diffSummary(original, edited): {added, removed, modified, reordered}
```

`toPatchPayload` is where "don't lose provenance" is enforced on the client too: it emits
`step_id` + changed keys only, so `source_refs`/`grounding_status` are never transmitted and
therefore never at risk of being echoed back wrong.

### 5.2 New components — `frontend/src/components/playbooks/`

| File | Responsibility |
|---|---|
| `playbook-editor-context.tsx` | draft state, dirty tracking, `beforeunload` guard, save/discard mutations |
| `playbook-steps-editor.tsx` | the ordered list: add / duplicate / delete / move up / move down, keyboard (`Alt+↑/↓`), empty state |
| `playbook-step-editor.tsx` | one step card: instruction `Textarea`, `type` `Select`, expected outcome, on failure, reason, plus a collapsed **Advanced** block (`safety_class`, `action_type`, `action_name`, `tool_ref`, `requires_approval`, `reversible`, `verification`, `time_estimate_sec`, `rollback_hint`); renders read-only `source_refs` / `grounding_status` chips so the author can see what they must not misrepresent |
| `playbook-meta-editor.tsx` | title, description, risk tier (→ `PATCH /playbooks/{id}`) and rollback notes, execution guidance, trigger conditions (→ version PATCH) |
| `playbook-trigger-conditions-editor.tsx` | list editor over the `symptoms` / `conditions` / `entities` arrays, with a raw-JSON escape hatch for other keys |
| `playbook-save-bar.tsx` | sticky footer: "N unsaved changes · Discard · Save draft" + conflict banner |

**`components/common/playbook-steps.tsx` is not restructured.** It gains exactly one thing: an
"Edited" badge when `step.human_edited`. The reviewer's read-only view cannot regress because it
is not the edit view.

Validation client-side via **zod + react-hook-form** (already dependencies) mirroring §4.2, so
the obvious errors never reach the network.

**Reordering:** v1 uses move-up / move-down buttons + keyboard, which adds **no dependency** and
is accessible by default. Drag-and-drop (`@dnd-kit/sortable`) is an optional follow-up; if added
it must sit on top of the same `moveStep` function.

### 5.3 Detail page — `app/(dashboard)/playbooks/[id]/page.tsx`

- Local `mode: "view" | "edit"` state. **Default is `view`; every existing panel renders exactly
  as today in `view`.**
- Edit affordance in the "Procedure steps" `CardAction`:
  - hidden unless `canEditPlaybook(roles)`
  - **draft + current version** → `Edit` (enters edit mode)
  - **published version** → `Edit as new draft` (calls the fork endpoint, then selects the new
    version and enters edit mode) with a confirm dialog explaining that the approved version keeps
    serving runtime until the draft is approved
  - **older, non-current version selected** → disabled + tooltip "Switch to the latest version to
    edit"
  - lifecycle `retired`/`deprecated` → disabled + tooltip
- In edit mode: version `Select`, `Diff` and `Rollback` are disabled (they would discard state).
- Save → `PATCH .../versions/{vid}` → on success `invalidateQueries(["playbook-versions", id])`
  **and** `["playbook", id]` and `["playbooks"]`, toast, back to `view`.
- On 409 `revision_conflict`: keep the user's edits in state, show a banner with
  "Reload latest / Overwrite anyway"-style choice (Overwrite = refetch, re-apply the diff onto the
  new base, re-submit with the fresh revision — never a blind force).
- Title/description become editable in `PageHeader` area via `playbook-meta-editor`.
- Fix B2 (`comment` → `comments`) or delete the dead `TransitionDialog`.

### 5.4 List page — `app/(dashboard)/playbooks/page.tsx`

No functional change required. Optional: a pencil quick-link in the row actions to
`/playbooks/{id}?edit=1`. Keep the bulk-transition behaviour untouched.

### 5.5 Types & roles

- `lib/types/index.ts` — `PlaybookVersion` gains `revision`, `updated_at`,
  `derived_from_version_id`, `created_by`, `last_edited_by`, `is_editable`, `verification_policy`
  (B4). All optional so cached responses keep type-checking.
- `lib/roles.ts` — add:

```ts
/** Can edit a playbook's content (steps, triggers, notes). Not automation mode. */
export const canEditPlaybook = (roles: string[]) => isKnowledgeManager(roles);
```

  This mirrors the backend gate exactly. `canEditAutomationMode` stays `tenant_admin`.

---

## 6. Versioning & audit semantics (the one-page answer)

| Action | `semantic_version` | `revision` | `published_at` | Runtime sees |
|---|---|---|---|---|
| Save edit to a draft | unchanged | +1 | stays NULL | nothing (still previous published) |
| Edit an approved playbook | new patch bump on the **fork** | fork starts at 1 | fork NULL | still the approved version |
| Approve (`under_review → approved`) | unchanged | unchanged | set now | the newly published version |
| Rollback | new patch bump | 1 | set if playbook approved | the rolled-back content |

Events emitted:

| Event | Store | Payload |
|---|---|---|
| `playbook.version_edited` | `operational_events` + `audit_logs` | revision, changed_fields, `{added, removed, modified, reordered}`, edit_note |
| `playbook.version_forked` | both | source version id, new semantic_version |
| `playbook.version_discarded` | both | version id |
| `playbook.updated` | `audit_logs` | changed fields on the playbook row |

Existing `playbook.version_created`, `playbook.transitioned`, `memory.playbook_promoted` are
unchanged. Because a draft's *content* is mutable, the audit trail — not the row — is the record
of what changed; that is why the summary payload is mandatory rather than optional.

---

## 7. Non-regression checklist (run before each commit, per `CLAUDE.md` pass 2)

- [ ] `GET /runtime/playbooks/{stable_key}` returns the same version before and after editing a draft
- [ ] `/runtime/match` ranking unchanged for an approved playbook with an open draft
- [ ] `graph/agent` projection still falls back to newest published
      (`test_playbook_steps_projection.py::test_unpublished_current_falls_back_to_newest_published`)
- [ ] `playbook.embedding` unchanged while `lifecycle_state == "approved"`
- [ ] `search/playbook_candidates.py` + `hybrid_ranker` unaffected (they read published versions)
- [ ] `PlaybookVersionResponse` still accepts both `dict` and `list` `evidence_refs`
- [ ] transition guard still blocks a zero-step version
- [ ] `SupportCopilot` side panel (port 8099 → 8001) renders the same steps for an approved playbook
- [ ] MAF `integrations/maf/playbook_client.py` + `playbook_tools.py` unaffected
- [ ] diff endpoint output unchanged except for the added `verification_policy` key

---

## 8. Tests

**Backend** — `backend/tests/`, mock-style per `conftest.py` (no live DB):

`test_playbook_version_edit.py`
- patch updates steps / trigger_conditions / rollback_notes and bumps `revision`
- **unknown keys survive**: a stored step with `source_refs` + `grounding_status` + a vendor key
  keeps all three after an edit to `text`
- new step gets `step_id`, `grounding_status: non_grounded`, `step_classification: human_authored`
- editing a grounded step's text sets `human_edited` and keeps `source_refs`
- deleting a step by omission removes exactly that step
- reorder rewrites `order` to `1..n` and matches array position
- `expected_revision` mismatch → 409 with `current_revision`
- published version → 409; non-current version → 409; `retired` → 409
- unknown `tool_ref` → 422 (regression for B1)
- step over 4 000 chars → 422; 101 steps → 422; duplicate `step_id` → 422
- audit + operational event written with the summary
- `embed_playbook` **not** called when `lifecycle_state == "approved"`

`test_playbook_version_fork.py`
- fork copies payload incl. `verification_policy`, sets `derived_from_version_id`
- published source row untouched (`published_at`, `steps` identical)
- `current_version_id` repointed to the fork
- second fork call returns the existing draft (idempotent)
- `resolve_published_playbook_version` still returns the original

`test_playbook_version_response.py` — extend for the new fields.

**Frontend** — vitest (`npm run test`):
- `lib/playbook-steps.test.ts` — merge preserves unknown keys, `moveStep`, `toPatchPayload`
  emits only changed keys, `diffSummary`
- `components/playbooks/playbook-steps-editor.test.tsx` — add / delete / reorder / dirty state
- `components/common/playbook-steps.test.tsx` — unchanged, must still pass

**Manual** on `http://localhost:3000/playbooks/929e3ef9-f1b6-4660-9df4-92c32e9d3bd1`:
edit a step → save → reload → verify; add a step → submit for review → approve → confirm
`/runtime/playbooks/{key}` serves the new content only after approval.

Run `python -m pytest -q` from `backend/` and record the count in the commit message
(`CLAUDE.md` pass 3).

---

## 9. Delivery phases

| Phase | Content | Ships independently |
|---|---|---|
| **P0** | Migration `0093`, model columns, response fields, B1/B3/B4 fixes | ✅ invisible |
| **P1** | `services/playbook_editing.py` + `PATCH /versions/{id}` + tests | ✅ API-only |
| **P2** | `lib/playbook-steps.ts` + types + `canEditPlaybook` + unit tests | ✅ no UI change |
| **P3** | Step editor: edit existing steps, save, dirty guard, 409 handling | ✅ **first user-visible win** |
| **P4** | Add / duplicate / delete / reorder steps | ✅ |
| **P5** | Meta editor: title, description, risk tier, trigger conditions, rollback notes, guidance | ✅ |
| **P6** | Fork flow for approved playbooks + optional discard-draft | ✅ |
| **P7** | `codewiki/` entry, RUNBOOK note, B2 cleanup, three review passes | ✅ |

Each phase is behind `canEditPlaybook(roles)`, so partial rollout is safe without an env flag. If
a hard kill-switch is wanted, gate the Edit button on
`process.env.NEXT_PUBLIC_PLAYBOOK_EDITING !== "0"`.

---

## 10. Files touched

**Backend**
```
alembic/versions/0093_playbook_version_editing.py      NEW
src/contextedge/models/playbook.py                     +5 columns
src/contextedge/schemas/playbook.py                    +PlaybookStepPatch, +PlaybookVersionUpdate,
                                                       +PlaybookVersionForkRequest, response fields
src/contextedge/services/playbook_editing.py           NEW  (normalize_steps, validate_steps)
src/contextedge/api/v1/playbooks.py                    +PATCH version, +fork, (+DELETE draft),
                                                       B1 catch, B3 _version_payload
tests/test_playbook_version_edit.py                    NEW
tests/test_playbook_version_fork.py                    NEW
tests/test_playbook_version_response.py                extended
```

**Frontend**
```
src/lib/playbook-steps.ts                              NEW
src/lib/playbook-steps.test.ts                         NEW
src/lib/types/index.ts                                 PlaybookVersion fields
src/lib/roles.ts                                       +canEditPlaybook
src/lib/api.ts                                         read detail.message on dict details
src/components/playbooks/*.tsx                         NEW (6 files, §5.2)
src/components/common/playbook-steps.tsx               +"Edited" badge only
src/app/(dashboard)/playbooks/[id]/page.tsx            edit mode wiring, B2 fix
src/app/(dashboard)/playbooks/page.tsx                 optional quick-edit link
```

**Docs**
```
codewiki/<n>-playbook-editing.md                       NEW (per repo convention)
```
