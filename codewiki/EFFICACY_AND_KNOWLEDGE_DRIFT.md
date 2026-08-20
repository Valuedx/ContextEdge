# Efficacy: did the fix actually work, and does the documentation still hold?

**Status:** shipped 2026-08-21. Roadmap E1, E2 and E3, pulled forward from position 9 in the sequence.
**Companion docs:** [INCIDENT_DIAGNOSIS_ROADMAP](INCIDENT_DIAGNOSIS_ROADMAP.md), [07-episodes-patterns-playbooks](07-episodes-patterns-playbooks.md), [SITUATION_CORRELATION](SITUATION_CORRELATION.md), [KNOWN_GAPS](KNOWN_GAPS.md).

---

## Summary

A pattern could say how often it was cited and never how often it helped. `PatternEvidence` — the ledger G3 built for exactly this — had `outcome` NULL on all 1,551 rows, not because the data was missing but because episode outcomes are free text in **9,014 distinct phrasings**.

E1 normalizes those into `success | partial | failure | unknown`, writes them into the ledger, and aggregates them into per-pattern efficacy, a confidence class, and the query that finds documented advice the record contradicts.

Measured on the reference corpus: **1,416 empirical ledger rows** classified (697 success, 66 partial, 132 failure, 521 unknown) across **533 patterns** — 429 `EMPIRICAL`, 75 `DOCUMENTED_ONLY`, 29 `MIXED`. Mean success rate **76.9%** over the 330 patterns with a computable one.

## Business picture

This is the capability an August 2026 competitive review found nobody ships. Three vendor categories track three different things and market them as one:

| | question | who does it |
| --- | --- | --- |
| activity | was the action item closed? | common |
| relevance | did the user like the answer? | common, sophisticated |
| **efficacy** | **did the fix work?** | **essentially absent** |

The sharpest illustration is ServiceNow's Article Health Score, which grades every knowledge article 0–100 on image alt tags (17%), multiple H1 tags (16%), bad links (17%), article length (17%), title relevancy (17%) and readability (16%). An article recommending a restart that fails four times in five scores **100**.

The barrier protecting this is not technical — ServiceNow already stores `kb_use`, `reopen_count` and `close_code`. It is that measuring a knowledge base's failure rate is commercially unattractive to the vendor who sold you the knowledge base.

## Walkthrough

### The unnormalized column

10,247 of 15,260 episodes carry a `final_outcome`. "Resolved", "Resolved.", "Issue resolved.", and "Issue resolved, ticket closed." are one outcome in four spellings, and there are 9,014 of them. Nothing could aggregate that, so nothing did.

### The classifier, and its two traps

Deterministic and ordered — same rule as situation correlation: an outcome is a factual claim, and a model's opinion is not evidence for one.

**`"unresolved"` contains `"resolved"`.** A contains-check in the obvious order classifies every failure in the corpus as a success and inflates every number downstream, all of which look entirely plausible. Failure rules run first; a test pins the ordering.

**Closed is not fixed.** "Ticket closed due to lack of client response" is not a fix that did not work — nothing was tried and nothing was learned. Counting it as failure understates efficacy, as success overstates it. It maps to `unknown` and leaves the rate alone.

### Measured coverage, reported rather than assumed

| | count | share |
| --- | --- | --- |
| success | 4,442 | 43.3% |
| partial | 471 | 4.6% |
| failure | 1,099 | 10.7% |
| *deliberately declined* (closed / abandoned / info-only) | 950 | 9.3% |
| *unrecognised* — the real gap | 3,285 | 32.1% |

**67.9% recognised by some rule.** Splitting "declined" from "unrecognised" matters: both surface as `unknown` and mean opposite things about the classifier — one is a rule choosing not to call it, the other is a coverage gap. Reported as one number, a declining classifier looks like a failing one.

The remaining 32% is dominated by *process states* — "meeting scheduled to discuss the issue", "fix identified and planned for AE 8.2.5 patch release", "guidance provided" — which are honestly neither success nor failure. Tuning further would be over-fitting to this corpus's idioms.

### Rate arithmetic

Denominator is success + partial + failure. Two choices worth stating because both are easy to get quietly wrong:

- **`unknown` is excluded, not counted as failure.** Otherwise an unclassifiable corpus drives every rate toward zero and reads as fixes that stopped working.
- **A pattern with no rate-bearing outcomes has `success_rate = None`, not 0.0.** "We do not know" and "it never works" must not share a representation.
- **Partial counts in the denominator but not the numerator.** Restoring service with a workaround is not fixing the cause.

### Knowledge drift, and a negative result

The headline query: patterns carrying documented support whose observed outcomes fall below the threshold, worst first.

**On the reference corpus it returns zero, and that is a real finding rather than a broken rule.** The mechanism is exercised — 15 of the 29 `MIXED` patterns clear the ≥5 rate-bearing sample bar, so all 15 were evaluated. None fell below 50%. The lowest observed rates are 60.0%, 61.1% and 66.7%.

Recorded so it is not re-litigated: on this corpus, documented resolutions hold up.

## Decisions

**Deterministic classification, not a model.**
*Why:* an outcome is a factual claim about what happened. Rules are inspectable, orderable, and cheap enough to re-run over the whole corpus — which matters because the rules will change.
*Tradeoff:* 32% of outcome text goes unrecognised, and a model would classify most of it. It would also classify some of it wrongly and invisibly, and a wrong outcome is worse than an absent one: absent is excluded from the rate, wrong silently moves it.

**`unknown` for anything unrecognised, never a guess.**
*Why:* the rate is the product. Protecting it from noise is worth losing coverage.
*Tradeoff:* the corpus looks less classified than it is, and a reader could mistake declining for failing — which is why the two are reported separately.

**Rollups computed on read, not stored.**
*Why:* the inputs change whenever an episode is reconstructed or an article is attached. A stored rollup drifting from its ledger would be wrong in exactly the direction that looks like a finding.
*Tradeoff:* every read pays a query over the ledger. At 1,551 rows this is nothing; at a million it needs a materialized view, and this will have to be revisited rather than scaled.

**`dry_run=True` is the default on the backfill.**
*Why:* it rewrites a column ranking will read. A rule change should be measured on real data before it moves any number — which is how the 67.9% figure exists at all.
*Tradeoff:* an operator who forgets the flag gets a no-op and may think it ran. The result dict reports `written` explicitly for that reason.

**Drift requires documentation to drift *from*.**
*Why:* a purely empirical pattern with a low success rate is a hard problem, not stale knowledge. Flagging it sends someone to edit an article that does not exist.
*Tradeoff:* a genuinely bad undocumented remediation is not surfaced by this query at all.

**Thresholds are named and untuned.**
`DRIFT_SUCCESS_RATE = 0.5`, `MIN_DRIFT_SAMPLE = 5`. There is no labelled drift set to tune against, and inventing one would make a chosen number look measured. The sensitivity is real and stated: the lowest observed rate is 60.0%, so a threshold of 0.65 would flag one pattern where 0.5 flags none.

## E2 and E3: where the measurement reaches the recommendation

E1 alone measures efficacy that nothing acts on — a report, not a capability. E2 (can it apply here?) and E3 (what is known to go wrong?) are what turn it into a decision, and all three are fused in `remediation_advisory_service`.

### Both items named machinery that holds no rows

Same lesson as E1. `fix_applicability_rules`, `fix_patterns`, `fix_cohort_stats` and `case_outcome_fix_patterns` are all **0 rows**, as are the `invalidated_fix` edges E3 was specified against. What exists instead is better.

**Applicability** lives in the `applicability` JSON on knowledge cases and evidence — 764 rows — and its dimensions are very unevenly filled:

| dimension | populated | of 764 |
| --- | --- | --- |
| `deployment` | 764 | 100% |
| `components` | 719 | 94% |
| `product_versions` | 148 | 19% |
| `version_floor` | 57 | 7.5% |
| `version_ceiling` | 39 | 5% |
| `environments` | 12 | 1.6% |
| `platforms` | 0 | 0% |

E2's headline case — "rule out a fix that does not match the incident's product version" — therefore rests on 7.5% coverage. Deployment and component matching carry the weight. The engine handles every dimension and is honest that two of them currently decide anything.

A pattern has no applicability column of its own; the statements live on the knowledge cases that document it, reached through the ledger. That is the right home — "this applies to on-prem 8.2 and above" is a claim a *source* makes, and attaching it to the pattern would lose which source made it.

**Negative knowledge** lives in `episode_steps`, which carries `failed_flag`, `successful_flag` and `result_state` — 970 steps flagged failed, structured rather than the `[did not work]` text markers E3 assumed. Measured: 217 patterns carry 510 failure statements.

### Precedence, because the columns disagree

`result_state` and the booleans conflict on **378 of 24,245** steps: 331 say `result_state='failure'` with `failed_flag=false`, 33 claim `successful_flag=true` *and* `result_state='failure'`, 14 the reverse. `result_state` wins — it is the richer vocabulary and the booleans cannot express `inconclusive` at all, so false-in-both is ambiguous by construction where `result_state` is not. The 33 rows asserting both are dropped rather than resolved: a step claiming both is evidence of neither.

### Exclusion is the conservative direction

An `excluded` verdict suppresses a remediation, and when that is wrong the failure is **silent** — the operator never learns the option existed. So exclusion requires both sides to state a value and to actually conflict. Silence yields `unknown`, which is visible in the output.

Component mismatch deliberately does *not* exclude: those vocabularies are LLM-extracted free text, so absence of overlap is as likely to be a naming difference as a real mismatch.

### Measured: the verdicts move with context

Over 533 patterns on the reference corpus:

| context | recommend | with caution | insufficient | do not recommend |
| --- | --- | --- | --- | --- |
| none given | 8 | 78 | 447 | 0 |
| on-prem, AE 8.2.3 | 8 | 75 | 429 | **21** |
| cloud | 8 | 58 | 370 | **97** |

A cloud context suppresses 97 patterns documented for on-prem. That is E2 doing the only thing it exists for.

The output is a sentence rather than a score: *"100% success across 15 outcome-bearing episodes; support: MIXED; 5 known failure(s)"*. That pairing is not a contradiction — the **episodes** resolved, while individual **steps within them** failed on the way. Surfacing both is the point of E3: this works, and here is what people tried first that did not.

## Code map

| Path | Role |
| --- | --- |
| `services/outcome_classification.py` | ordered rules; `classify_outcome_detailed` splits declined from unrecognised |
| `services/efficacy_service.py` | ledger backfill, per-pattern rollup, confidence class, drift |
| `api/v1/patterns.py::pattern_efficacy` | `GET /api/v1/patterns/efficacy` |
| `api/v1/patterns.py::knowledge_drift` | `GET /api/v1/patterns/knowledge-drift` |
| `tests/test_efficacy_and_outcomes.py` | the ordering trap, the arithmetic, the drift guards |
| `services/remediation_advisory_service.py` | E2 applicability, E3 negative knowledge, fused verdict |
| `api/v1/patterns.py::advise` | `POST /api/v1/patterns/advise` |
| `tests/test_remediation_advisory.py` | mostly: the exclusions that must not happen |

## Acme VPN incident (this layer)

The KB article documenting the `AUTH_CERT_EXPIRED` fix (reissue the intermediate CA with SHA-256) and the one recommending a `radius-auth` restart for `AUTH_TIMEOUT` are both `documented` support in the ledger. The first is corroborated by the resolved incident; the second sits against three recurrences that each applied the workaround and each came back a week later.

That second article is the shape drift detection exists for: approved upstream, formatted perfectly, scoring 100 on every cosmetic measure, and contradicted by every observation of it. On this corpus it has too few outcome-bearing episodes to cross the sample threshold — which is the rule working, not failing.

## References

- Roadmap E1, and E2/E3 which build on this ledger: [INCIDENT_DIAGNOSIS_ROADMAP](INCIDENT_DIAGNOSIS_ROADMAP.md)
- The ledger's schema and its CHECK constraint: `models/pattern.py::PatternEvidence`
- The epistemic split this measures over: [KNOWN_GAPS](KNOWN_GAPS.md), 2026-08-20 section
