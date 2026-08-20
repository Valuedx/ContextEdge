# Coverage: telling "nothing happened" from "nothing here can see"

**Status:** shipped 2026-08-21. Roadmap H2, with the declarative capability layer folded in.
**Companion docs:** [INCIDENT_DIAGNOSIS_ROADMAP](INCIDENT_DIAGNOSIS_ROADMAP.md), [SERVICENOW_LIVE_VERIFICATION](SERVICENOW_LIVE_VERIFICATION.md), [03-ingestion-connectors-and-sync](03-ingestion-connectors-and-sync.md), [KNOWN_GAPS](KNOWN_GAPS.md).

---

## Summary

An agent asks whether a change caused an incident, and gets an empty list. Before this, that empty list meant one of eight different things and looked identical in all eight. The agent had no way to tell "no change caused this" from "nothing connected here can see a change", so it reported the first — confidently, and sometimes wrongly.

Coverage reports every facet of what the deployment holds, with a status that says which of those worlds you are in, and a `blind_spots` list naming the facets where an empty result must not be read as a zero. Underneath it is a canonical capability declaration: one place that states what each connector can reach, cross-checked by tests against the reference services that actually emit it.

## Business picture

The failure this prevents is not a crash. It is a diagnosis that sounds well-grounded and is built on an absence of instrumentation.

"No recent changes were made to this gateway" is a strong claim. It is the difference between escalating to the network team and closing the ticket as user error. If the sentence is generated because a change connector was never configured, the agent has manufactured evidence out of a gap — and nothing in the output distinguishes it from the same sentence generated after actually checking 237 change records.

Coverage makes the second sentence available: *I cannot see changes on this deployment.* That is a worse answer and a much better one.

## Walkthrough

### The eight statuses

Each one implies a different next move, which is the test for whether a distinction earns its place. If two statuses would lead to the same action, they should be one status.

| Status | What it means | What to do about it |
| --- | --- | --- |
| `not_configured` | no source connected at all | connect something |
| `unsupported` | no connected connector can supply this | this question cannot be asked here |
| `unavailable` | the connector supports it; this *instance* does not expose it | install the module |
| `not_selected` | the instance exposes it, nobody approved it for sync | tick the box |
| `pending` | approved, no sync has succeeded yet | wait |
| `empty` | synced, and there is genuinely nothing | **believe the zero** |
| `stale` | rows exist, last sync is older than the freshness window | re-sync before concluding |
| `available` | rows exist and are fresh | read the number |

Only `empty`, `stale` and `available` are `answerable`. The rest land in `blind_spots`.

The `unavailable` / `not_selected` split is the one that repays the effort. ServiceNow's `em_alert` needs ITOM Event Management; a stock instance does not activate it, so the Table API answers `400 Invalid table` and discovery steps over the table. Reporting that as "not approved for sync" sends an operator to a checkbox that does not exist. The two are distinguished by asking whether *discovery* ever wrote a source object, which is the instance answering the question directly.

### Facets, not a percentage

A single "coverage: 70%" number would be worse than nothing. It averages away the one dimension that matters for the question being asked, and the missing dimension is never the same one twice. Ten facets are reported: `incidents`, `changes`, `problems`, `knowledge`, `requests`, `monitoring`, `topology`, `causal_links`, `duplicate_links`, `ownership`.

The last three are backed by graph relations rather than by records, because "are there changes" and "does anything record which change caused an incident" are different questions with different answers. A deployment can hold both incidents and changes and still have no connector able to assert a causal link between them.

### Live output

Against the connected ServiceNow instance, after the scenario fixtures were ingested:

```text
facet            status          count
incidents        available         105
changes          available         105
problems         available          39
knowledge        available           7
requests         available           2
monitoring       unavailable         0   <- ITOM not activated on this instance
topology         available          65
causal_links     available           3
duplicate_links  available           5
ownership        available         112

blind spots: ('monitoring',)
```

One honest blind spot, correctly attributed to a missing module rather than a missing checkbox or a missing connector.

## The capability layer

Coverage needs to know what a connector *could* supply, which is a question about the connector, not about any record. That knowledge existed, scattered: a dict in the ServiceNow reference service, issue-link-type branches in Jira, a tuple list in Zoho, inline literals in SapphireIMS. Fine for emitting edges, useless for answering "could this ever emit one".

`services/source_capabilities.py` states it once, in canonical terms — and splits the statement two ways depending on whether the knowledge already exists somewhere authoritative.

**Record kinds are derived.** `evidence_typing._OBJECT_TYPE_MAP` already maps `(source_type, object_type) → canonical evidence type`, and that map is what normalization actually applies. A second copy would be a second opinion about the same fact, so `record_kinds_for()` reads it. A connector that learns a new object type gains the capability with no edit here.

**Relations are declared, and tested against reality.** There is no single structure to read them from, so they are written down — and `tests/test_source_capabilities.py` cross-checks each declaration against the string literals in the reference service that would emit it, in both directions. Declaring a relation the code cannot emit fails; emitting one that is not declared fails. Docstrings are excluded from the comparison, because several of these modules discuss relations they do not emit (Jira's header explains that its `caused_by_change` is "the same edge type ServiceNow emits"), and prose would otherwise satisfy the check.

Current declarations:

| Connector | Relations | Topology |
| --- | --- | --- |
| `servicenow` | affects_ci, assigned_to_group, caused_by_change, remediated_by_change, related_problem, child_of_incident, preceded_incident | yes |
| `jira_sm` | affects_ci, caused_by_change, remediated_by_change, related_problem, duplicate_of | no |
| `zoho_desk` | affects_ci, assigned_to_group | no |
| `sapphireims` | affects_ci | no |
| `teams`, `gmail`, `local_file`, `manageengine` | none | no |

## Decisions

**Declare capability; do not refactor the five reference services onto a shared mapping.**
*Why:* their differences are real. Jira resolves relations from issue-link-type strings at runtime; ServiceNow reads static reference fields. Flattening those into one table would either lose Jira's semantics or bend everyone else's around them. What coverage needs is the *capability*, not the mechanism.
*Tradeoff:* the declaration can drift from the code, so it is only as good as the test that checks it. That test is therefore not optional — it is the mechanism, and deleting it silently converts this module into documentation.

**Derive record kinds, declare relations.**
*Why:* duplicate knowledge drifts. Anything already stated authoritatively is read rather than repeated.
*Tradeoff:* the two halves of one declaration now live in two places and read inconsistently, which is worth one paragraph of explanation to avoid a class of bug.

**No "optional object types" field on the declaration.**
*Why:* whether an instance exposes a table is not a property of the connector. Discovery already answers it exactly — it writes a source object per object type the instance exposes — so its absence is a measurement, not an assumption.
*Tradeoff:* the answer now depends on discovery having run. A source that has never been discovered reports `unavailable` for everything, which is true but reads more alarmingly than "not discovered yet".

**Narrow by object type only where the connector's objects are named after its object types.**
*Why:* ServiceNow is the only connector whose discovery writes one source object per *table*, so `external_id` is literally `incident` or `change_request`. Teams names objects `team:channel`, Gmail a mailbox, Zoho `tickets:<department>`, Jira a project key. Narrowing by object type is precise on ServiceNow and matches nothing anywhere else — which would report every facet on a Teams or Jira deployment as `unavailable`, inventing exactly the false blind spot this module exists to prevent.
*Tradeoff:* on those connectors the facet falls back to source-level sync state, so it cannot say "this channel is not synced". Less precise, but it never invents an absence — and between the two failure modes, only one produces a confident lie.

**Tenant-wide, not domain-scoped.**
*Why:* coverage answers "what can this deployment see at all", a property of instrumentation rather than of the records a given reader may read. Domain-scoping it would give two agents different blind spots for the same instrumentation, and a blind spot that varies by who is asking is a permissions artefact wearing a blind spot's clothes.
*Tradeoff:* `count` figures are tenant-wide totals, so a domain-limited reader sees numbers larger than the records they could retrieve. Counts here are evidence that a facet is populated, never a result set.

## Code map

| Path | Role |
| --- | --- |
| `services/source_capabilities.py` | the canonical declaration; `record_kinds_for`, `object_types_for`, `capability_for` |
| `services/coverage_service.py` | facet computation, the eight-way status decision, `CoverageReport` |
| `api/v1/graph.py::coverage` | `GET /api/v1/graph/coverage` |
| `tests/test_source_capabilities.py` | drift tests in both directions; registry completeness |
| `tests/test_coverage_service.py` | the status decision, answerability, connector-shape fallback |

## Acme VPN incident (this layer)

Asked about the `AUTH_CERT_EXPIRED` incident on `vpn-gw-east-01`, this deployment can now say precisely what it is standing on. Changes: `available`, 105 records — so "the KB5032190 change preceded this incident" is a checked claim. Duplicate links: `available`, 5 — the five reports really are one occurrence, asserted by `parent_incident`, not guessed from similar wording. Topology: `available`, 65 edges — the blast radius through `acme-vpn-service` is walkable.

And monitoring: `unavailable`. So if the agent is asked whether an alert fired before the tickets arrived, the honest answer is not "no alerts fired". It is that this deployment has no eyes there.

## References

- Roadmap item H2 and the sequencing it unblocks: [INCIDENT_DIAGNOSIS_ROADMAP](INCIDENT_DIAGNOSIS_ROADMAP.md)
- Why `em_alert` is absent here: [SERVICENOW_LIVE_VERIFICATION](SERVICENOW_LIVE_VERIFICATION.md)
- The canonical evidence-type map coverage derives from: `services/evidence_typing.py`
