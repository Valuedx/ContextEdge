# Situation lifecycle: recovery is evidenced, never inferred from silence

**Status:** shipped 2026-08-21. Roadmap H8.
**Companion docs:** [SITUATION_CORRELATION](SITUATION_CORRELATION.md), [DIAGNOSTIC_CONTEXT](DIAGNOSTIC_CONTEXT.md), [EFFICACY_AND_KNOWLEDGE_DRIFT](EFFICACY_AND_KNOWLEDGE_DRIFT.md), [KNOWN_GAPS](KNOWN_GAPS.md).

---

## Summary

Until now a situation could only ever start. H8 gives it the rest of its life — `emerging → active → stabilizing → resolved`, plus reopen, recurrence and merge — under one governing rule:

> **Absence of signal is never recovery.**

Verified live: the canonical VPN situation moved `active → resolved` because all six members carry a resolution in ServiceNow, then `resolved → reopened` the moment one stopped, with the recovery timestamps cleared.

## Business picture

The rule sounds pedantic until you consider what silence actually means. Tickets stop arriving when the thing is fixed, when everyone gave up, when the reporters went home for the weekend, and when a connector broke. Only one of those is recovery, and nothing in the silence distinguishes them.

A system that auto-resolves on quiet produces a clean dashboard and a wrong MTTR, and it does so most confidently at exactly the moment its ingestion has failed. So a situation with no new signals for a week and no resolved members stays `active`. That looks wrong on a wallboard and is the only honest reading.

## Walkthrough

### The transitions

| to | requires |
| --- | --- |
| `stabilizing` | at least one member carries a resolution, not all |
| `resolved` | **every** member carries a resolution in the source system |
| `reopened` | a situation that had recovered gains an unresolved member |

`cancelled` is deliberately not a resolved state: a withdrawn report is not a fixed problem, and counting it as one resolves situations nobody fixed.

`merged` and `invalidated` are never moved by automatic evaluation. They are decisions somebody made, and recomputing over a person is how a system teaches people that deciding is pointless.

### Reopen is not recurrence

The S1/S5 distinction, one level up.

**Reopen** — the same occurrence resumed. The situation keeps its identity, onset and history, and its recovery timestamps are cleared, because they described a recovery that did not hold and leaving them makes the next MTTR read from a moment the situation was not over.

**Recurrence** — the same failure happening again, as a *new* situation linked by `recurred_from`. Different occurrence, same shape.

Collapsing them loses whichever number you were about to quote. A recurrence treated as a reopen yields one situation with an onset weeks in the past and an MTTR spanning the gap between two unrelated outages. A reopen treated as a recurrence doubles the incident count and hides that the first fix did not hold — which is exactly the signal the efficacy ledger exists to catch.

### Merge preserves lineage

Memberships move rather than duplicate; a signal already in the survivor is **retired** rather than deleted, because the record that it was once filed under the other situation is the lineage the merge is meant to preserve. The loser keeps pointing at its survivor, and the database enforces it — verified live, a `merged` row with a null survivor raises `IntegrityError`.

Merge is governed by a role rather than a score. It rewrites what somebody may already have acted on.

### Split is deliberately absent

One situation that turns out to be two is a real case and an unsafe automation. A split proposal is safe; an automatic split silently rewrites history, and afterwards there is no way to tell which half a reader saw. v1 leaves it to a human, per the roadmap.

## Decisions

**Only positive resolution evidence moves a situation toward resolved.**
*Why:* the alternative is auto-resolving on quiet, which is most confidently wrong exactly when ingestion has broken.
*Tradeoff:* situations accumulate in `active` on any source that does not populate `case_state`, and the backlog looks like a bug. It is the honest reading, and the coverage facet is where a reader learns why.

**Reopen clears `resolved_at` and `stabilizing_at`.**
*Why:* they described a recovery that did not hold.
*Tradeoff:* the duration of the first recovery is lost. Preserving it needs a transition history table, which is real work and not what H8 asked for.

**Merge retires duplicate memberships instead of deleting them.**
*Why:* which situation a signal was originally filed under is the lineage.
*Tradeoff:* membership rows accumulate, and any consumer that forgets to exclude `retired` will double-count. Every query here excludes it; nothing enforces that a new one will.

**Terminal states are never recomputed.**
*Why:* `merged` and `invalidated` are human decisions.
*Tradeoff:* a situation merged in error stays merged until a human unwinds it, and no unwind exists.

## Code map

| Path | Role |
| --- | --- |
| `services/situation_lifecycle_service.py` | `assess_lifecycle` (pure), evaluation, merge, recurrence linkage |
| `api/v1/graph.py::evaluate_situation_lifecycle` | `POST /graph/situations/lifecycle` |
| `api/v1/graph.py::merge_situation` | `POST /graph/situations/{id}/merge`, role-gated |
| `workers/correlation_tasks.py::evaluate_situation_lifecycle_task` | schedulable, safe to run often |
| `tests/test_situation_lifecycle.py` | the rule, and the reopen/recurrence split |

## Acme VPN incident (this layer)

The situation that H3 assembled and H6 explained now finishes. All six members closed in ServiceNow, so it moved to `resolved` on evidence — not because the tickets stopped. When one member was reopened upstream, it returned to `reopened` and dropped its recovery stamps rather than reporting an outage that had ended and quietly restarted.

Had the same `AUTH_CERT_EXPIRED` failure appeared three weeks later, it would have been a *new* situation carrying `recurred_from` back to this one — not this one reopening, and not an outage that lasted a month.

## References

- Roadmap H8, and the split it declines to automate: [INCIDENT_DIAGNOSIS_ROADMAP](INCIDENT_DIAGNOSIS_ROADMAP.md)
- The state vocabulary and the merge CHECK constraint: `models/situation.py`
- Why `recurred_from` and `merged_into` already existed: `graph/edge_types.py`, registered with H1
