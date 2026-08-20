# ServiceNow, live: what a connected ITSM source turns on, and what it exposed

**Status:** connected and ingesting 2026-08-21 against a ServiceNow developer instance. First ServiceNow connection on any ContextEdge deployment.
**Companion docs:** [03-ingestion-connectors-and-sync](03-ingestion-connectors-and-sync.md), [09-graph-and-correlation](09-graph-and-correlation.md), [INCIDENT_DIAGNOSIS_ROADMAP](INCIDENT_DIAGNOSIS_ROADMAP.md), [ZOHO_DESK_CONNECTOR](ZOHO_DESK_CONNECTOR.md), [KNOWN_GAPS](KNOWN_GAPS.md).

---

## Summary

Four ServiceNow capabilities had shipped as code and never run against a live instance: reference-field enrichment (Phase 1), the CMDB topology hybrid (Phase 2), alert rollups (Phase 3), and change-risk scoring (Phase 4). Every deployment note in KNOWN_GAPS said the same thing — *code only on this deployment, zero rows*. Connecting a real instance replaced four "should work" claims with measurements, turned on the change and topology joins the situation workstream depends on, and surfaced two ingest defects that only a second connector could reveal.

It also settled which roadmap items are actually blocked. Three of the four connector-blocked items are now unblocked. The fourth, monitoring, stays blocked and now has a reason more specific than "no connector".

## Business picture

An incident arrives. Before this, ContextEdge could tell you which past tickets looked like it. It could not tell you that a change had been applied to the same gateway forty minutes earlier, that the gateway carries the remote-access service four hundred people use, or that the last three occurrences of a neighbouring symptom were closed with a workaround that keeps not holding. Those are the facts an engineer reaches for first, and they live in change management and the CMDB — systems ContextEdge could read but had never been pointed at.

The value is not "more records". It is that a question like *what else was going on around this?* stops being unanswerable and starts being answerable-or-honestly-empty.

## Walkthrough

### What the instance supports

Discovery answered with six tables and skipped one. That skip is the connector's `discover_objects` fallback doing exactly what it documents — a 400 on an absent table is logged and stepped over rather than failing the whole discovery.

| Table | Rows | What it unblocks |
| --- | --- | --- |
| `incident` | 325 | the corpus |
| `change_request` | 237 | **B1** — change evidence, and with it the `incident → CI ← change` join |
| `cmdb_ci` | 2,804 | **C1/C2** — CI entities, criticality, owner |
| `cmdb_rel_ci` | 250 | **C1** — dependency edges, blast radius |
| `problem` | 43 | **D3** — `aggregated_by`, recurrence |
| `kb_knowledge` | 57 | knowledge cases |
| `em_alert` | *absent* | **H5 stays blocked** |

`em_alert` ships with ITOM Event Management, which is not activated here, so the Table API answers `400 Invalid table`. This is a sharper statement than the roadmap's previous one: H5 is not blocked on writing a monitoring connector — the connector exists and handles rollups. It is blocked on an instance that has the plugin.

### What the ingest produced

Counts after the first backfill, against capabilities the codewiki had recorded as empty:

| | documented before | measured now |
| --- | --- | --- |
| evidence typed `change` | 0 | 39 |
| `configuration_item` entities | 0 (all 849 were `topic` / `knowledge_category`) | 28 |
| `affects_ci` edges | 0 | 32 |
| `depends_on` edges | **0 rows exist** | 19 |

The `depends_on` edges are worth dwelling on. Nobody asked for them. `cmdb_topology_service` warms a CI's neighbourhood when correlation meets a stale CI reference, so pointing the connector at a real CMDB made the topology cache populate itself. C1 was described as work; a large part of it turns out to be already wired and merely starved.

### Two defects the second connector exposed

Both had been invisible with one connector because they need a source whose records get *discarded* often enough to notice, and a conversational source to contrast against.

**`source_type` was a side effect of chunking.** `evidence_items.source_type` was written in exactly one place — the chunking dispatch — which sits behind the `not_relevant >= 0.75` extraction gate. Confidently-irrelevant evidence therefore kept `source_type = NULL` permanently, although the `Source` row is loaded three lines above the constructor. The split was exact on both corpora: 11 of 106 rows on the first ServiceNow ingest, 3,805 of 10,547 (36%) on zoho_desk. The 43 `not_relevant` zoho_desk rows that *do* carry a source_type are the confirmation — those scored below 0.75, so they never skipped, so they reached the stamp.

Any grouping or filter over source silently omitted that population — which is exactly the evidence a reviewer auditing *what did this connector throw away* is looking for, and exactly the field the facet work filters on.

**The message-function classifier never ran.** The larger consequence of the same ordering. The gate

```python
if not skip_extraction and (ev.source_type or "") in MESSAGE_FUNCTION_SOURCE_TYPES:
```

reads `ev.source_type` about ninety lines above the only line that set it. On a freshly constructed evidence row the value was always NULL, so the gate was always False. `classify_message_function` has one caller, behind that gate. Confirmed against the live corpus: **0 of 10,547 rows carry a `message_function`.**

Its four consumers have all been reading NULL — correction supersession (`correlation_service`), the dissociation veto and reply inheritance (`ticket_bridge_service`), and telemetry-based outcome verification (`execution_verification_service`). KNOWN_GAPS records A1 as shipped and later upgraded; the classifier was shipped, and the ingest path that feeds it was not reachable.

Stamping the column at construction fixes both. See the Decisions below for what that means for cost.

## Decisions

**Stamp `source_type` at construction rather than repairing rows after the fact.**
*Why:* a backfill fixes the rows that exist and leaves the mechanism intact, so the next irrelevant record lands NULL again. The `Source` is already loaded at the constructor, so the correct write costs nothing — no extra query, no new failure mode.
*Tradeoff:* rows normalized before the fix keep their NULL until re-normalized. The chunking stamp is kept as a documented backstop for them rather than deleted, at the price of two places that can write the column — mitigated by a test asserting the backstop stays labelled a backstop, so nobody restores it as the only writer.

**Let the message-function revival happen, and say so, rather than shipping it silently or suppressing it.**
*Why:* the gate was always meant to run; suppressing it would mean writing code to preserve a bug. On this deployment it is inert — `CONVERSATIONAL_SOURCE_TYPES` is `{teams, gmail, local_file}` and the only connected source is `servicenow` — so no unmeasured model spend lands here.
*Tradeoff:* the first deployment to connect Teams or Gmail pays one classification call per message, and four downstream behaviours change at once. That is a measure-first change under CLAUDE.md and it cannot be measured here, so it is recorded as owed rather than done.

**Author scenario fixtures instead of relying on the PDI's demo data.**
*Why:* a PDI's ~600 records are randomly generated. No change precedes the incident it caused, no CI depends on another, no incident duplicates its neighbour. Correlation validated against them demonstrates that the code runs, which the roadmap explicitly calls the wrong test.
*Tradeoff:* fixtures are data someone wrote, so they can encode the author's assumptions and flatter the implementation. Mitigated by keeping all ~600 random records in the corpus as adversarial noise, and by giving every scenario a stated assertion including two that must **not** fire (S2, S4).

**Model change windows on the change record, not in `cmn_schedule`.**
*Why:* the instance refuses schedule spans over REST (`Schedule Item validate`), and the comparison that matters — approved window against actual execution — is two fields on one record. It also works against any ITSM source that does not publish a freeze calendar, which is most of them.
*Tradeoff:* an organisation-wide freeze calendar cannot be expressed this way, so "was this change inside a freeze" remains unanswerable; only "did this change run outside its own approved window" is.

## The fixtures

`backend/evals/fixtures/servicenow_scenarios.py`, idempotent and tear-downable, keyed on `correlation_id`. Built on the canonical Acme VPN incident so the scenarios join the narrative the rest of the codewiki uses rather than starting a parallel one.

| | Shape | The assertion it exists to support |
| --- | --- | --- |
| S1 | change → major incident → 5 duplicates → problem | H3 groups them into **one** situation; H6 ranks the change first; D3 reads `parent_incident` |
| S2 | unrelated change, unrelated CI, same window | H6 ranks it **below** S1's change — the precision test |
| S3 | incident on a dependency, symptoms on the dependent | H4/C1 — same-CI matching cannot connect these |
| S4 | four unrelated incidents on one shared DC | H3 **must not** collapse them |
| S5 | known error + 3 recurrences weeks apart | recurrence is not one occurrence; H8 must not read it as a reopen |
| S6 | requested item + catalog task | the request lane is distinguishable, not assumed absent |
| S7 | one article documenting S1's fix, one recommending a workaround S5 contradicts | knowledge drift — documented advice against observed outcomes |
| S8 | change approved for a weekend slot, executed Thursday morning | two changes now touch the same CI; ranking must prefer the one whose *execution* is near the incident |

S1 and S5 are the pair that matters most. S1's five children share a `parent_incident`: one occurrence seen five times in three hours. S5's three incidents share a `problem_id` and nothing else: three occurrences weeks apart of one unresolved known error. A correlator that treats "shares a problem" as "is the same situation" merges them and reports a three-week outage that never happened.

### Instance constraints, measured

Four ServiceNow guardrails shaped the fixtures, and each one reads as a permissions error while being something else:

- **State models are not settable over REST.** `Change Model: Check State Transition` and `Problem Model: Check State Transition` reject every transition including the initial insert, with and without the model's mandatory planning fields. A change posted with `state=3` lands in `New`. Execution is expressed through `start_date`/`end_date` and `work_start`/`work_end` instead.
- **Data Policies mask themselves as 403s.** `assigned_to` is mandatory on a problem past `New`; the incident close field is `close_code`, which a Data Policy labels "Resolution code". An off-list `close_code` is stored as empty and then trips the policy as a *missing mandatory field* — the error names the field, never the fact that the value was rejected.
- **An unknown field in `sysparm_query` is dropped, not rejected.** The query runs without that term and matches everything. Keying `kb_knowledge` on `correlation_id` — a field that table does not have — selected an arbitrary demo article and the upsert tried to overwrite it. Only an ACL stopped the write. The fixture client now reads its key back and compares; a response missing the field is an error, not a miss.
- **`cmn_schedule_span` writes are refused** (`Schedule Item validate`), which is why change windows live on the change record.

## Code map

| Path | Role |
| --- | --- |
| `connectors/servicenow/connector.py` | `TABLES`, discovery with per-table skip, keyset backfill |
| `services/servicenow_reference_service.py` | reference fields → case links, typed edges, CI / group entities |
| `services/cmdb_topology_service.py` | live ±1-hop CMDB fetch, write-through cache, 7-day TTL |
| `workers/extraction_tasks.py::_normalize` | where `source_type` is now stamped |
| `evals/fixtures/servicenow_scenarios.py` | the S1–S8 builder, `--build` / `--teardown` |
| `tests/test_evidence_source_type_stamp.py` | ordering tests for both defects |

## Acme VPN incident (this layer)

This is the layer where the canonical incident stops being an example and becomes rows. `vpn-gw-east-01` is a real CI with real dependants — `acme-vpn-service` depends on it, it depends on `radius-auth-01`, it runs on `esx-host-04`. The KB5032190 change is a real `change_request` against that CI, ending at 02:00. The `AUTH_CERT_EXPIRED` incident opens forty minutes later carrying `caused_by` pointing back at the change and `problem_id` pointing at the certificate-chain problem, with five duplicates behind it.

Every claim the earlier articles make about this incident — that the change is findable from the incident, that the blast radius is walkable, that the duplicates are one occurrence — is now a query against data rather than a description of intent.

## References

- Roadmap items B1, C1, C2, D3, H3–H8: [INCIDENT_DIAGNOSIS_ROADMAP](INCIDENT_DIAGNOSIS_ROADMAP.md)
- Deployment notes this document supersedes: [KNOWN_GAPS](KNOWN_GAPS.md), Phase 1–4 resolved sections
- Connector contract precedent: [ZOHO_DESK_CONNECTOR](ZOHO_DESK_CONNECTOR.md)
