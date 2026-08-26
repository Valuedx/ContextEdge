# Playbook editing

You will see how a knowledge manager can change playbook steps without breaking the live procedure: drafts are editable, published versions stay frozen, the newest current version is the main editing target, and search embeddings plus runtime keep serving the last approved content until the draft itself is approved.

## Business picture

Playbooks used to be generated and then locked. Reviewers could read them and move them through lifecycle states, but they could not fix a wrong step, add a missing check, or explain why they changed the wording. The result was either a stale procedure or a flood of new version numbers for every save.

Editing is now a governed draft. An unpublished version can be changed in place. Saving does not publish. An optional **why this changed** note is stored on the audit row, not laundered into the procedure as if the model had written it. When the playbook is already approved, Edit creates a new draft that becomes the **main** version for authors, while agents, Support Copilot, and `/runtime/match` keep using the published version. Approving the draft is what flips runtime and the semantic fingerprint to the new content.

## Technical walkthrough

1. **Draft-mutable, published-immutable.** `playbook_versions.published_at IS NULL` rows can be patched. A published row's `steps` remain blocked by `trg_playbook_versions_steps_immutable`. In code: `PATCH /playbooks/{id}/versions/{vid}` in `backend/src/contextedge/api/v1/playbooks.py`, merge in `services/playbook_editing.py`.
2. **Top version is main.** `create_playbook_version` and the fork endpoint repoint `playbooks.current_version_id` at the new row. `GET .../versions` returns that current row first. The UI labels it `main`. Runtime ignores that pointer when it is unpublished.
3. **Merge, never replace.** Step patches are partial dicts matched by `step_id`. Unknown stored keys (`source_refs`, `grounding_status`, vendor fields) stay. Human-authored steps are labelled `non_grounded` / `human_authored`. Editing grounded instruction text sets `human_edited` and leaves citations intact.
4. **Embeddings stay version-correct.** Approved playbooks keep the published fingerprint while a draft is open (N3). Candidates re-embed the current/top draft so the first approve is not the first semantic write. Approve always re-embeds the version being published. Support Copilot hydrates **published** versions only, even if `current_version_id` is a draft.
5. **Optimistic concurrency.** PATCH requires `expected_revision`. Mismatch returns 409 with `current_revision` so the UI can reload or rebase.

## Example: Acme VPN data at this stage

**Input** (what arrives) — knowledge manager edits the approved VPN playbook:

```json
{
  "expected_revision": 1,
  "edit_note": "Call out the east-1 gateway; west-1 is already rotated.",
  "steps": [
    {"step_id": "s1", "text": "Check certificate expiry on vpn-gw-east-01"}
  ]
}
```

**Output** (what the system produces) — a new unpublished main version; runtime still serves v1.0.0:

```json
{
  "semantic_version": "1.0.1",
  "revision": 1,
  "published_at": null,
  "is_editable": true,
  "derived_from_version_id": "…v1.0.0 id…",
  "current_version_id": "…this draft…"
}
```

`GET /runtime/playbooks/{stable_key}` still returns the published v1.0.0 steps until `under_review → approved`.

## Design decisions

- **In-place draft vs a new row per save.** Version numbers stay meaningful; audit carries the change history.
- **No separate drafts table.** Every consumer already resolves published vs current; duplicating the schema would split that rule.
- **Edit notes are audit-only.** They explain the change without rewriting generation provenance.

## Code map

| Concern | Module | Key symbol |
| --- | --- | --- |
| Merge + validation | `backend/src/contextedge/services/playbook_editing.py` | `normalize_steps`, `validate_steps` |
| PATCH / fork / discard | `backend/src/contextedge/api/v1/playbooks.py` | `update_playbook_version`, `fork_playbook_version_draft` |
| Published fingerprint | `backend/src/contextedge/services/playbook_embedding.py` | `resolve_published_playbook_version`, `embed_playbook` |
| Runtime published version | `backend/src/contextedge/api/v1/runtime.py` | `_resolve_runtime_published_version` |
| Agent projection | `backend/src/contextedge/graph/agent/repository.py` | `_published_versions_for_playbooks` |
| Copilot hydration | `SupportCopilot/backend/app/insights.py` | `hydrate_playbook_rows` |
| Editor UI | `frontend/src/components/playbooks/` | `PlaybookEditor` |

## Acme VPN incident (this layer)

When Acme's VPN playbook is already approved and an engineer notices the west-1 gateway line is wrong, they fork a draft, save with a why-changed note, and runtime still recommends the published procedure until a reviewer approves the draft.

## Further reading

- [07-episodes-patterns-playbooks.md](./07-episodes-patterns-playbooks.md)
- [06-ai-extraction-and-embeddings.md](./06-ai-extraction-and-embeddings.md)
- [15-dashboard-and-operator-workflows.md](./15-dashboard-and-operator-workflows.md)
