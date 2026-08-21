# One incident in, the operational context around it out

**Status:** shipped 2026-08-21. Roadmap H7 — the acceptance criterion the roadmap was written for.
**Companion docs:** [SITUATION_CORRELATION](SITUATION_CORRELATION.md), [CHANGE_CORRELATION](CHANGE_CORRELATION.md), [COVERAGE_AND_CAPABILITY](COVERAGE_AND_CAPABILITY.md), [EFFICACY_AND_KNOWLEDGE_DRIFT](EFFICACY_AND_KNOWLEDGE_DRIFT.md), [KNOWN_GAPS](KNOWN_GAPS.md).

---

## Summary

`GET /api/v1/graph/diagnostic-context/{incident_evidence_id}`. One identifier in, seven facets out, each with its own status and provenance, plus the list of things whose emptiness must not be read as a zero.

This composes rather than computes. Situations from H3, change candidates from H6, criticality and owner from C2, efficacy and negative knowledge from E1–E3, honesty about gaps from H2. The value is not new inference — it is that the answer arrives as one bounded, provenanced object instead of nine queries somebody has to know to run.

Live, on the canonical incident:

```text
incident   : VPN authentication failing for remote users - AUTH_CERT_EXPIRED
occurred   : 2026-08-10T02:40:00+00:00

  [available] situation    n=1   One of 6 signals describing a single occurrence.
  [available] impact       n=4   1 directly affected, 3 one hop away.
                                 Highest stated criticality: 1 - most critical (acme-vpn-service).
  [available] duplicates   n=5   Impact scale, not 5 separate problems.
  [available] changes      n=2   2 ranked, 1 confirmed by the source system.
  [empty    ] recurrence   n=0   A problem record is linked, but no other incident shares it.
  [available] remediation  n=1   Ranked by whether defensible; 1 carries known failures.
  [available] coverage     n=1   1 dimension cannot be answered here: monitoring.

BLIND SPOTS: ['monitoring']
```

## Business picture

The difference is what a responder holds thirty seconds after the page. Before: a ticket title and a description. After: this is one occurrence reported six times, it touches a service rated most-critical, a change went onto the same gateway seventy minutes earlier and the source system already blames it, here is the remediation with its measured success rate and what is known to fail — **and we cannot see monitoring here, so do not read "no alerts" as "no alerts fired"**.

That last clause is the one that makes the rest safe to act on.

## Walkthrough

### Facets, not a blob

Each facet answers independently and carries its own status, provenance, count, note and truncation flag. A bundle that merges everything into one payload cannot say *which part* is missing, and a reader cannot tell a quiet estate from an unconfigured one.

| facet | question | provenance |
| --- | --- | --- |
| `situation` | is this one occurrence among many? | H3 |
| `impact` | what does it touch, and how much does that matter? | C1 / C2 |
| `duplicates` | who else reported this same thing? | reference edges |
| `changes` | what changed near it, ranked? | H6 |
| `recurrence` | has this root cause been seen before? | `related_problem` |
| `remediation` | what to do, how well it works, what fails | E1–E3 |
| `coverage` | what can this deployment not see at all? | H2 |

### The bug worth recording

The first version reported **`blind_spots: []`** on a deployment with no monitoring connector.

`blind_spots` listed only facets that *failed to answer*, while the `coverage` facet separately reported dimensions the deployment cannot answer at all — and coverage had answered successfully, about not being able to answer. Two different absences with the same consequence, kept in two places, so the field a reader checks for reassurance gave it falsely.

They are now one list. A facet that could not answer for this incident and a dimension this deployment cannot answer at all both appear, because a reader who treats either as "none found" concludes something the data does not support. This is exactly the confusion H2 exists to remove, and it had been reintroduced one layer up.

### Bounded, because the reader has a budget

Every facet is capped, and a truncated facet says so rather than quietly returning a prefix. Caps are per facet so one noisy dimension cannot crowd out the rest — on a busy CI, whatever is numerous is rarely what matters.

### Security-filtered, and it failed closed only at the door

The incident lookup honoured `allowed_domain_ids` from the start. Nothing else did: duplicates, recurrence and change candidates returned records regardless of domain, so a restricted reader would have seen titles and timestamps outside their scope. A bundle described as security-filtered that filters only its entry point is worse than one making no such claim.

Scoping now runs through every record-bearing facet. Domain-NULL evidence is deliberately excluded from a restricted view: NULL is the encoding for reviewed tenant-global knowledge, and unassigned ingest rides the same convention, so including it would leak un-scoped records. Verified live — a reader limited to a domain the incident is not in receives no bundle at all.

## Decisions

**Compose, do not recompute.**
*Why:* every facet already has an owner with its own tests and thresholds. Reimplementing any of it here would create a second answer that drifts from the first.
*Tradeoff:* the bundle is as slow as the sum of its parts and re-runs change correlation on every call. Caching it would need invalidation on six upstream tables, which is worse than the latency until measurement says otherwise.

**Merge the two kinds of absence into one `blind_spots`.**
*Why:* they differ in cause and are identical in consequence. Separated, the field reads as reassurance.
*Tradeoff:* the caller loses the distinction between "this facet failed here" and "this deployment cannot answer that". Both remain recoverable from the facet statuses; only the summary is merged.

**Coverage last.**
*Why:* a reader who has scanned the facts above is exactly the reader about to draw a conclusion, and that is the moment to say which conclusions are unavailable.
*Tradeoff:* it is also the easiest position to stop reading before.

**Exclude domain-NULL evidence from restricted views.**
*Why:* NULL means tenant-global, and unassigned ingest lands there too. Including it would leak whatever nobody has scoped yet.
*Tradeoff:* a restricted reader cannot see genuinely tenant-global knowledge either, which is a real loss and the safe direction.

## Code map

| Path | Role |
| --- | --- |
| `services/diagnostic_context_service.py` | the bundle, the facets, the merged blind spots |
| `api/v1/graph.py::diagnostic_context` | `GET /graph/diagnostic-context/{id}` |
| `tests/test_diagnostic_context.py` | blind-spot merging, scoping, bounding |

## Acme VPN incident (this layer)

This is where the incident stops being a ticket. Handed `AUTH_CERT_EXPIRED` and nothing else, the system returns: one occurrence seen six times, onset 02:40; `acme-vpn-service` at *1 - most critical* reached one hop from the gateway; the KB5032190 change seventy minutes earlier, `confirmed` by ServiceNow's own `caused_by`; five duplicate reports that are impact scale rather than five problems; a remediation ranked on measured efficacy with its known failures attached; and one honest gap — no monitoring here, so silence from the alerting layer means nothing.

Every earlier article describes what its layer contributes to this incident. This is the layer where all of them arrive at once.

## References

- The roadmap item and what it depended on: [INCIDENT_DIAGNOSIS_ROADMAP](INCIDENT_DIAGNOSIS_ROADMAP.md)
- Why `monitoring` is the standing blind spot here: [SERVICENOW_LIVE_VERIFICATION](SERVICENOW_LIVE_VERIFICATION.md)
