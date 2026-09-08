# Situation correlation: which signals describe one occurrence

**Status:** shipped 2026-08-21. Roadmap H3, in fuller form than planned.
**Companion docs:** [INCIDENT_DIAGNOSIS_ROADMAP](INCIDENT_DIAGNOSIS_ROADMAP.md), [COVERAGE_AND_CAPABILITY](COVERAGE_AND_CAPABILITY.md), [SERVICENOW_LIVE_VERIFICATION](SERVICENOW_LIVE_VERIFICATION.md), [09-graph-and-correlation](09-graph-and-correlation.md).

---

## Summary

Six tickets arrive about the VPN. They are one occurrence, and until now nothing said so — each got its own diagnosis, its own reviewer, its own count in every statistic built on incident volume.

`correlate_situations` assembles incident evidence into `OperationalSituation` rows, deterministically and without an LLM. The interesting half is what it refuses to merge: a shared problem, a shared CI, the same failure three weeks later. Over-merging does not degrade the answer, it fabricates one — a three-week outage that never happened, indistinguishable downstream from a real one.

Measured on the live corpus: 51 groups considered, **one** situation created (six members, all authoritatively linked), 50 singletons left alone, one genuine hub CI suppressed.

## Business picture

The difference is what a responder sees when the sixth ticket lands. Without situations: a queue of six unrelated-looking VPN complaints, six people potentially picking them up, and an incident count that says six things broke. With situations: one occurrence, opened 02:40, six reports, still active — and the next ticket that matches joins it rather than starting a seventh investigation.

The refusals matter just as much. A known error that recurs every Monday is one problem and *many* situations. Reporting it as one long outage would be worse than reporting nothing: it would make the MTTR, the impact window and the recurrence count all wrong at once, and every one of those numbers would look plausible.

## Walkthrough

### What merges

**Authoritative links.** `child_of_incident` and `duplicate_of` are written by a human in the source system who looked at both records and said they are the same thing. Better evidence than anything inferable, so it produces a `confirmed` membership and an `active` situation.

**Same CI + inside the window + symptom agreement.** All three, producing an `inferred` membership and an `emerging` situation. Any two of these are satisfied constantly by unrelated work on shared infrastructure.

### What deliberately does not merge

**A shared problem.** `related_problem` is authoritative too, and asserts something different: same root *cause*, which spans occurrences by definition. This is the most tempting wrong join available — the edge is right there, it is human-authored, and using it produces a confident, plausible, false answer. It is named in `NON_MERGING_EDGE_TYPES` rather than merely omitted, so the exclusion is visible to whoever extends the list next.

**A shared CI.** A domain controller serves password resets, DNS complaints, GPO failures and disk alerts in one afternoon.

### Vetoes

| Veto | Status on this corpus |
| --- | --- |
| Time window (24h between signals) | live — separates the recurrences from the storm |
| Hub CI (>8 incidents over the lookback) | live — suppressed `PolicyAdminService`, 12 incidents in three days |
| Environment mismatch | **inert** — `source_facets` is empty on every row, so nothing states an environment to disagree about |

### The occurrence-time defect this surfaced

H3's window veto did nothing at first, and the reason was upstream. Evidence `created_at_source` was `sys_updated_on` for every ServiceNow record — when someone last *touched* it, not when the thing happened. An incident opened in January and re-assigned yesterday looked like it happened yesterday, and every record from one backfill looked simultaneous.

Measured: the fixture incidents all carried 2026-08-20 19:0x, their ingest minute, against real `opened_at` values spanning 2026-07-20 to 2026-08-13.

`EVENT_TIME_FIELDS` now derives the evidence timestamp per table — `opened_at` for incidents and problems, `work_start` then `start_date` for changes, `initial_event_time` for alerts — falling back to `sys_updated_on`. The checkpoint keeps using `sys_updated_on`, which it must: it is the only monotonic cursor.

After the fix, the same records:

| Record | timestamp | from |
| --- | --- | --- |
| KB5032190 change | 2026-08-10 01:30 | `work_start` |
| major incident | 2026-08-10 02:40 | `opened_at` |
| S8 ACL change | 2026-08-13 09:00 | `work_start`, not its approved 08-07 window |

That last row is the one H6 will need: the *executed* time, not the paperwork.

### Idempotency, found in review

The first implementation created a fresh situation on every run — two runs over an unchanged corpus produced two situations and twelve memberships for one six-ticket occurrence. A scheduled run would have minted a new outage every tick.

Identity is **overlap, not set equality**: one member already placed means this is that occurrence seen again, with more signals. A set-hash would call the grown situation a different one and mint a second row the moment a seventh ticket arrived. The `fingerprint` column records the set so an unchanged run can recognise itself and write nothing; it is not the lookup key.

## Decisions

**Deterministic, no LLM.**
*Why:* a merge is a factual claim about the world, and a model's opinion is not evidence for one. Every decision here is a join or a comparison.
*Tradeoff:* symptom agreement is limited to signals that already exist as structured data, so two tickets describing the same failure in different words will not merge unless something authoritative links them. A model would catch those — and would also merge things that merely sound alike, which is the failure that cannot be undone.

**Overlap, not set equality, as situation identity.**
*Why:* a situation accumulates signals. Set equality would fragment one occurrence into a new row per arriving ticket.
*Tradeoff:* two situations that both overlap a new group are not merged — the earliest onset wins and the other is left alone. Collapsing them is a merge, merge needs lineage, and lineage is H8's. Until then, a genuinely split occurrence stays split.

**`related_problem` excluded from merging, and named as excluded.**
*Why:* same cause is not same occurrence. S1 and S5 exist in the fixtures precisely to hold this line.
*Tradeoff:* an occurrence whose *only* linkage is a shared problem will not be assembled, even when it is real. Under-merging costs a missed grouping; over-merging costs a fabricated outage.

**Hub CIs cannot anchor an inferred merge at all.**
*Why:* on shared infrastructure "same CI" carries almost no information, and the false-merge rate rises with the CI's popularity.
*Tradeoff:* conservative on exactly the CIs where real situations are most likely. `PolicyAdminService` has 12 incidents in three days with related-sounding symptoms — plausibly one situation, and this refuses to say so without an authoritative link or a shared signature.

## Code map

| Path | Role |
| --- | --- |
| `services/situation_correlation_service.py` | grouping, vetoes, idempotent write |
| `connectors/servicenow/connector.py::EVENT_TIME_FIELDS` | occurrence time per table |
| `workers/correlation_tasks.py::correlate_situations_task` | tenant-wide trigger |
| `api/v1/graph.py::list_situations` | `GET /api/v1/graph/situations` |
| `tests/test_situation_correlation.py` | the refusals, mostly |
| `evals/fixtures/servicenow_scenarios.py` | S1/S4/S5, the shapes under test |

## Acme VPN incident (this layer)

This is the layer where the six VPN reports stop being six things. `AUTH_CERT_EXPIRED` on `vpn-gw-east-01` is now one situation: `active`, `incident_storm`, confidence 0.9, onset 02:40, last signal 05:00, six members all `confirmed/authoritative`. The KB5032190 change sits 70 minutes before onset, waiting for H6 to rank it.

And three incidents on `radius-auth-01` — the same `AUTH_TIMEOUT` signature, the same documented workaround, sharing a problem record — remain three situations, weeks apart, which is what they are.

## References

- Roadmap H3, and H8's lifecycle/merge work this defers to: [INCIDENT_DIAGNOSIS_ROADMAP](INCIDENT_DIAGNOSIS_ROADMAP.md)
- Why the environment veto has no data: [COVERAGE_AND_CAPABILITY](COVERAGE_AND_CAPABILITY.md)
- Schema and invariants (migration 0074): `models/situation.py`
