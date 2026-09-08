# Which change caused this? A ranked list, never a verdict

**Status:** shipped 2026-08-21. Roadmap H6.
**Companion docs:** [SITUATION_CORRELATION](SITUATION_CORRELATION.md), [SERVICENOW_LIVE_VERIFICATION](SERVICENOW_LIVE_VERIFICATION.md), [EFFICACY_AND_KNOWLEDGE_DRIFT](EFFICACY_AND_KNOWLEDGE_DRIFT.md), [KNOWN_GAPS](KNOWN_GAPS.md).

---

## Summary

The first question anyone asks when an incident lands. Until ServiceNow supplied change records with real execution times, it was unanswerable.

Given a situation, `correlate_changes_for_situation` walks its affected CIs, expands one dependency hop, and ranks every change in the window on an explainable additive model. Measured on the canonical incident: two candidates, correctly ordered — the KB5032190 change on the same CI at `confirmed`, and a RADIUS timeout change *one hop away* at `candidate` 0.55.

Building it also uncovered a defect that had silently stopped change ingestion entirely.

## Business picture

Without this, "was there a change?" is a human walking the change calendar and eyeballing timestamps. With it, the incident arrives already carrying its suspects, each with a stated reason: *touches the same CI; executed 70 minutes before onset; the source system records this change as the cause.*

The one-hop expansion is what makes it more than a same-CI lookup. A change to `radius-auth-01` can break `vpn-gw-east-01` without ever touching it, and that is precisely the case a human under pressure misses — nothing in the gateway's own change history mentions it.

## Walkthrough

### A ranking, not a probability

`correlation_score` is a rank under an additive model, capped at 1.0, with every contributing factor recorded in `score_breakdown`:

| factor | weight |
| --- | --- |
| touches the same CI | 0.50 |
| touches a CI one dependency hop away | 0.25 |
| executed within 2h before onset | 0.30 |
| within 24h | 0.15 |
| within the window | 0.05 |
| executed outside its approved window | 0.10 |

0.85 means "strong on those factors", never "85% likely to be the cause". Anything rendering it must use candidate language. The day a calibrated probabilistic model exists it gets its own column rather than quietly redefining this one.

### Confirmation comes from governance, never from the score

`confirmed` is reachable only from something governed — here, a ServiceNow `caused_by` reference a human filled in, recorded in `confirmation_basis` with what asserted it. No score, however perfect, promotes a candidate. Allowing that would let inference launder itself into fact, and the next reader could not tell what somebody asserted from what something computed.

### A change after onset cannot be the cause

Enforced in the database as well as the code: the schema refuses `temporal_relation='after_onset'` together with a causal status. Post-onset changes on the affected CI are still recorded, as `remediation` — usually somebody fixing it, and what was tried matters even when it is not the cause.

### Out-of-window execution

A sharper signal than proximity: plenty of changes happen near an incident, far fewer happened at a time nobody approved. Computed from the change's own `start_date`/`end_date` (approved) against `work_start`/`work_end` (actual).

It is computed at correlation time rather than read from `source_facets`, which is empty on every row in this corpus — `derive_facets` only populates when a source declares `facet_fields` and the ServiceNow source declares none. Scoring on a facet nothing writes would be a factor that can never fire, which is indistinguishable from a factor that never matters.

### The defect this uncovered

H6 found no candidates at first, and the reason was six layers upstream.

`change_request` incremental sync had returned **zero rows on every run since 20:04**, reporting `completed` each time. The keyset checkpoint read `last_updated: 2035-05-28 12:30:56`.

A stock ServiceNow PDI contains exactly one record — `CHG0000003`, "Roll back Windows SP2 patch" — dated **nine years in the future**. The keyset is `sys_updated_on > checkpoint`, so once that row was consumed the cursor pinned to 2035 and nothing could ever exceed it again. One bad timestamp ended ingestion for an entire table, permanently, while every sync reported success.

The fix refuses to let a future-dated row become the checkpoint. The row is still ingested — it is a real record someone made — but the cursor will not follow it. Unparseable timestamps are deliberately *not* treated as future: doing so would stall the stream the same way the next time an upstream format changes.

## Decisions

**Rank everything in the window; suppress nothing.**
*Why:* unlike applicability (E2), a missing candidate here is invisible. An operator scanning a ranked list can argue with it; they cannot argue with a filter that dropped the row they needed.
*Tradeoff:* noisy estates produce long lists, and the ordering carries all the weight. A busy CI with many changes will surface many weak candidates.

**One dependency hop, not N.**
*Why:* it is the distance where "a change over there broke this" stays legible. Each further hop multiplies candidates while the causal story gets harder to state.
*Tradeoff:* a two-hop cause is invisible. The `esx-host-04 → vpn-gw-east-01 → acme-vpn-service` chain means a change to the ESX host is one hop from the gateway and two from the service, so a service-level situation would not see it.

**Derive affected CIs from membership rather than `situation_entity_impacts`.**
*Why:* that table is H4's and is empty. Deriving keeps H6 working now instead of waiting.
*Tradeoff:* two code paths will compute blast radius until H4 lands, and they can disagree.

**A future-dated row is ingested but never checkpointed.**
*Why:* it is a real record; refusing to store it would lose data over a data-quality problem.
*Tradeoff:* such a row is re-fetched and deduped on every subsequent sync — one wasted row per run, against a permanently dead stream.

**Never overwrite a reviewed candidate.**
*Why:* recomputing over a human is how a system teaches people that reviewing is pointless.
*Tradeoff:* a rejected candidate stays rejected even when the evidence changes underneath it, and nothing currently re-opens it.

**Thresholds are named and untuned.**
`SUSPECTED_SCORE = 0.7`, `CANDIDATE_SCORE = 0.4`. Chosen so same-CI plus close-in-time clears the bar while either alone does not. There is no labelled cause set to tune against, and inventing one would make a chosen number look measured.

## Code map

| Path | Role |
| --- | --- |
| `services/change_correlation_service.py` | blast radius, scoring, the status ladder, idempotent write |
| `connectors/servicenow/connector.py::_is_future` | the checkpoint guard |
| `workers/correlation_tasks.py::correlate_situation_changes_task` | tenant-wide trigger |
| `api/v1/graph.py::situation_change_candidates` | `GET /graph/situations/{id}/change-candidates` |
| `tests/test_change_correlation.py` | the ladder, the guard, malformed input |
| `evals/fixtures/servicenow_scenarios.py` | S1 same-CI, S2 control, S9 one-hop |

## Acme VPN incident (this layer)

The incident now arrives with its suspects attached:

```text
confirmed   1.00  dist 0  −70 min   Deploy Windows update KB5032190 to VPN gateway fleet
                                    touches the same CI; executed 70 minutes before onset;
                                    the source system records this change as the cause
candidate   0.55  dist 1  −100 min  Reduce RADIUS client timeout from 30s to 5s
                                    touches a CI one dependency hop away
```

The S2 control — a firmware refresh on `print-srv-02` fifty minutes before onset — never appears at all. It is not ranked low; it is not in the blast radius, so it is not a candidate. That is the discrimination the fixture exists to prove, and it is stronger than a low score would have been.

## References

- Roadmap H6, and the B4 same-CI lookup it supersedes: [INCIDENT_DIAGNOSIS_ROADMAP](INCIDENT_DIAGNOSIS_ROADMAP.md)
- Schema and the after-onset CHECK constraint: `models/situation.py`
- Why change execution times exist at all: [SITUATION_CORRELATION](SITUATION_CORRELATION.md), the occurrence-time section
