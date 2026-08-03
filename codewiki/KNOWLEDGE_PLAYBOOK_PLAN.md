# Plan: knowledge (KB/SOP) as a first-class playbook source

## Summary

A review proposed making the KB/SOP → playbook relationship a first-class workflow. This plan verifies each claim against the code, corrects the severity ordering, and sequences the work.

**Headline: every claim in the review is true.** Two are worse than described, and one systemic finding changes how the first fix should be scoped. The most severe defect is not the missing feature the review leads with — it is a retrieval path that has been silently returning nothing since it shipped.

## Verification

Each claim was checked against the code rather than accepted.

| # | Claim | Verdict | Evidence |
| --- | --- | --- | --- |
| 1 | KB articles are not typed `kb_article` | **Confirmed** | The ServiceNow connector never emits `evidence_type`; `extraction_tasks.py:251` defaults to `"message"` |
| 2 | ServiceNow KB gets ticket authority | **Confirmed** | `_default_authority(source_type)` takes only source type; `servicenow` → `"ticket"` unconditionally |
| 3 | `PatternEvidenceLink.evidence_id` is not populated | **Confirmed** | `pattern_service.py:99` sets `pattern_id`, `episode_id`, `link_type` only |
| 3b | → generated `evidence_ref_ids` is empty | **Confirmed** | `pattern_tasks.py:293` filters `{... for ln in links if ln.evidence_id}` — always empty for generated patterns |
| 4 | `PlaybookEvidenceLink` rows are not created | **Confirmed — worse** | Not instantiated **anywhere** in `src/` or `tests/`. Read in two places, written in none |
| 5 | KB content is not used in generation | **Confirmed** | `generate_playbook_candidate(pattern_title, pattern_description, episode_count, episode_summaries, negative_knowledge)` — no knowledge parameter exists |
| 6 | `KB_LONG_TERM_TYPES` never matches | **Confirmed** | `memory_service.py:28` defines `{"kb_article", "sop", "documentation"}`; nothing produces those types |
| 7 | The ranker depends on `PlaybookEvidenceLink` | **Confirmed — worse** | `vector_search.py:101` adds an **INNER** join when `playbook_id` is set |

### The two that are worse than stated

**#4 + #7 together are a silent total failure, not a weakening.** `PlaybookEvidenceLink` is never written, and playbook-scoped vector search inner-joins it. So any search scoped to a playbook returns **zero rows, always** — no error, no log line, and callers read it as "no supporting evidence found." The review describes this as "weaker than the schema suggests." It is not weaker; it is zero.

This outranks the KB-retrieval feature in severity, because a missing feature is visible and a feature that returns empty is not.

### The systemic finding that reframes gap #1

The review frames the evidence-type problem as a ServiceNow KB issue. It is not:

```
gmail        0 occurrences of evidence_type
teams        0
jira_sm      0
servicenow   0
sapphireims  0
zoho_desk    3   <- the only one
```

**No connector except `zoho_desk` has ever set `evidence_type`.** Every record from every other source normalizes to `"message"`. So `evidence_type` is not a field with a ServiceNow-shaped hole in it — it is an unpopulated field that one in-flight branch just started using.

That changes the fix from "add a mapping for ServiceNow KB" to "establish the contract, then adopt it per connector." The `zoho_desk` connector is the working template: it stamps `evidence_type` in the event content, and `chunkers/registry.py` and `extraction_tasks.resolve_synthesis_role` already branch on it.

### The dependency chain — this drives sequencing

Gaps 2 and 6 are **unfixable until gap 1 lands**, and this is not obvious from the review's ordering:

```
Gap 1: evidence_type is "message" for KB articles
   │
   ├──> Gap 2: _default_authority can't distinguish KB from incident
   │            (fixing the mapping alone changes nothing —
   │             evidence.evidence_type is still "message")
   │
   └──> Gap 6: KB_LONG_TERM_TYPES never matches
                (the set is correct; nothing produces its members)
```

Shipping the authority mapping or the memory-class fix first produces **zero observable change** and looks like the fix didn't work. Gap 1 must land first.

### Correction to one detail

The review proposes `source_authority = approved_sop or knowledge_article`. Note these are two different mechanisms that are easy to conflate:

- `source_authority` — **chunk** metadata, a reranker feature, set in `evidence_chunk_service._default_authority`
- `source_role` — **synthesis** authority, set in `extraction_tasks.resolve_synthesis_role`, consumed by the episode prompt's field-authority rules

The in-flight `feature/zoho-desk-connector` branch fixed the **second** for `kb_article` (`EVIDENCE_TYPE_ROLE_MAP`). It did **not** touch the first. Both need the treatment; they are separate call sites with separate vocabularies.

## Recommended sequencing

Three phases, each independently shippable. The review's proposals map onto phases 2 and 3; phase 1 is repair work the review's items depend on.

### Phase 1 — repair the dead paths (small, high value)

Nothing here is new capability. It makes existing, already-shipped features actually run.

**1.1 Establish the `evidence_type` contract.** Derive it from connector object type, per the review's mapping. Do it in one place — a `_derive_evidence_type(source_type, object_type, payload)` helper the normalizer calls — rather than asking six connectors to remember a convention. Connectors that already stamp it (`zoho_desk`) keep winning; the helper is the floor, not an override.

*Why here:* it gates 2 and 6, and it is a precondition for every knowledge feature in phases 2–3 — you cannot retrieve "approved SOPs" if SOPs are indistinguishable from chat messages.
*Tradeoff:* existing evidence rows keep `"message"`. A backfill is needed for historical KB articles, or knowledge features only see records ingested after the change. Recommend a one-off backfill task keyed on `RawEvidenceObject._connector_object_type`, which already records the truth.

**1.2 Make `source_authority` depend on evidence type.** Thread `evidence.evidence_type` (already in scope at `evidence_chunk_service.py:69`) into `_default_authority`, and add `knowledge_article` / `approved_sop` to the vocabulary.

*Tradeoff:* changes reranker features for existing chunks. Chunks are versioned by `chunker_version`, so a re-chunk is the clean path; without one, old and new chunks carry different authority for the same source.

**1.3 Write `PlaybookEvidenceLink` rows.** In `create_playbook_version`, materialize the JSON `evidence_refs` into normalized rows. This is the fix that turns playbook-scoped search from "always empty" into "works."

*Why not just drop the table:* two search paths already depend on it, and phase 2's link types need it. The table is right; the writer is missing.
*Tradeoff:* existing published playbooks have no rows. Either backfill from `evidence_refs` JSON or accept that only new versions are searchable.

**1.4 Resolve pattern evidence through `episode_evidence_links`.** Stop reading `PatternEvidenceLink.evidence_id` (never populated). Migration `0037` already maintains per-episode evidence grounding — the review is right that it should be the source of truth.

*Tradeoff:* one extra join in the generation path. Negligible against an LLM call.

**1.5 Guard the dead join.** When `playbook_id` is set and the link table yields nothing, log it rather than returning an empty result set indistinguishable from "no matches." A defect that returns `[]` silently is the reason this went unnoticed.

### Phase 2 — knowledge in generation, with honest disagreement (medium)

This is the review's core proposal and it is sound.

**2.1 Retrieve KB/SOP evidence for a pattern** after episode reconstruction, using the issue fingerprint (error signature, CI traits, technology) rather than the incident title. The review's point that retrieval must happen post-reconstruction is correct — that is when the real problem is known.

**2.2 Feed it to the generator** as a distinct input, not merged into episode summaries. `generate_playbook_candidate` gains a `knowledge_sources` parameter; the prompt gets a new version (never mutate a released prompt — the repo's convention).

**2.3 Surface disagreement rather than resolving it.** This is the highest-value idea in the review. When the SOP says "restart" and 15 verified episodes resolved without one, the generator must emit both and mark the step `requires_review`. Silently preferring either is the failure mode.

**2.4 Step-level citations** with `source_refs` distinguishing normative (SOP) from empirical (episode) grounding.

*Why phase 2 and not 1:* it is worthless until phase 1 makes KB articles identifiable and playbook evidence links real.
*Tradeoff:* larger prompts and more retrieval per generation. Bounded by capping retrieved knowledge chunks and filtering to applicable/current documents first.

### Phase 3 — knowledge health model (large; scope carefully)

The review proposes `knowledge_versions`, `knowledge_claims`, `knowledge_claim_validations`, alignment candidates with a lifecycle, per-dimension health scoring, applicability rules, and drift detection. This is a **quarter of work**, not a sprint, and it should not be committed to wholesale on the strength of a design note.

**Recommended first slice — claim-level validation, deferring the rest:**

The single most valuable piece is **empirical validation**: which KB procedures actually worked, on which cohorts, how often. That is measurable from data ContextEdge already has (episodes, outcomes, execution verification from migration `0036`), it needs no new LLM extraction, and it directly answers the question a reviewer asks. Per-dimension freshness scoring, applicability rule extraction, and version-diff drift detection can follow once there is evidence they pay for themselves.

*Why this slice:* it converts existing outcome data into a trust signal without first building a document-parsing pipeline.
*Tradeoff:* it does not catch a never-used-but-obsolete article. Freshness heuristics address that, and they are cheaper to add later than to unwind if the claim model is wrong.

**Endorsed without reservation:** the review's insistence that manual incident→KB links are unreliable and must be *discovered*, and its correction that KB articles must not be clustered into episodes. An article is not evidence that an incident occurred. That distinction should be written into the episode cluster resolver as an explicit exclusion, not left implicit — it is cheap now and expensive to retrofit.

**Flagged as premature:** the eleven-value knowledge-status enum and the ten-value link-type vocabulary. Both are plausible, and both will be wrong in detail until phase 2 shows which distinctions reviewers actually act on. Recommend starting with the three or four states that drive distinct runtime behavior (`current`, `current_with_gaps`, `contradicted`, `obsolete`) and growing the vocabulary from observed need.

## Recommended immediate action

**Phase 1 as one branch.** It is small, entirely repair, needs no schema design debate, and unblocks everything else. Concretely: 1.1 → 1.2 → 1.3 → 1.4 → 1.5, in that order, since 1.2 depends on 1.1.

Phase 2 should be specced against a real pattern in the live data once phase 1 lands, so the retrieval quality can be measured rather than assumed. Phase 3 should not start until phase 2 has shown which knowledge attributes reviewers actually use.

## Code map

| Concern | Module | Symbol | Phase |
| --- | --- | --- | --- |
| Evidence type default | `workers/extraction_tasks.py` | `evidence_type=payload.get(...)` (L251) | 1.1 |
| Connector object type | `models/evidence.py` | `RawEvidenceObject._connector_object_type` | 1.1 |
| Chunk authority | `services/evidence_chunk_service.py` | `_default_authority` (L131) | 1.2 |
| Synthesis role | `workers/extraction_tasks.py` | `EVIDENCE_TYPE_ROLE_MAP` | done (zoho branch) |
| Memory class | `services/memory_service.py` | `KB_LONG_TERM_TYPES` (L28) | 1.1 (unblocked) |
| Playbook provenance | `services/playbook_service.py` | `create_playbook_version` (L252) | 1.3 |
| Pattern links | `services/pattern_service.py` | `create_pattern_from_episodes` (L99) | 1.4 |
| Episode grounding | `models/episode.py` | `EpisodeEvidenceLink` (L280) | 1.4 |
| Playbook-scoped search | `search/vector_search.py` | inner join (L101) | 1.5 |
| Generation inputs | `ai/generators/playbook_generator.py` | `generate_playbook_candidate` | 2.2 |
| Contradictions | `services/contradiction_service.py` | existing | 2.3 |

## Acme VPN incident (this plan)

Today, if Acme's VPN certificate-renewal SOP is ingested from ServiceNow's `kb_knowledge` table, it becomes evidence typed `"message"` with `source_authority: "ticket"`, is excluded from long-term memory, is never retrieved during playbook generation, and — if it were somehow linked to a playbook — would be invisible to playbook-scoped search because the link table is never written. The playbook generated from Acme's VPN episodes would reflect what engineers did, silently omitting the SOP's backup-before-renewal step.

After phase 1 it is typed `kb_article`, carries knowledge authority, and is retrievable. After phase 2 the generator sees it, and a reviewer is shown "the SOP requires a backup step; no observed episode performed one" instead of a playbook that quietly drops it.

## Further reading

- [09-graph-and-correlation.md](./09-graph-and-correlation.md) — evidence links and correlation tiers
- [07-episodes-patterns-playbooks.md](./07-episodes-patterns-playbooks.md) — the episode → pattern → playbook path
- [KNOWN_GAPS.md](./KNOWN_GAPS.md) — deferred-work tracker
