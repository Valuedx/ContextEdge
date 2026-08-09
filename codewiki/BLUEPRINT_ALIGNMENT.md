# Incident Intelligence Blueprint — context-graph alignment

Verdict of the 2026-08-07 review of `Incident_Intelligence_Blueprint.md`
(§1.5 seven layers, §1.6 correlation primitives) against ContextEdge,
plus what was built in response. Rule of thumb the review confirmed:
**the distance to full coverage is dominated by connectors, not graph
capability** — the architecture (governed evidence graph, temporal
edges, confidence-first, CMDB-skeptical, projection-bounded) is the
same philosophy the blueprint argues for.

| Blueprint layer | Verdict | ContextEdge basis |
| --- | --- | --- |
| 1 Identity graph | structure yes, sources no | canonical identities + aliases + 0.95-gated adjudication; needs AD/Entra/IGA/HR connectors |
| 2 Infra & topology | yes (ServiceNow-scoped) | reference enrichment + demand-driven cmdb_rel_ci cache (TTL, edge closing) |
| 3 Service dependency map | yes + inferred | authored depends_on/runs_on/... projectable; **co_fails_with** inferred from case co-occurrence (dependency_inference_service; confidence refreshed and below-threshold edges expired each sweep); **proposed_depends_on** from agent discoveries — excluded from maf.v1 so agents never see unreviewed topology; review workflow at `/graph/edge-proposals` (approve promotes to authored depends_on with provenance, reject closes the proposal) |
| 4 Change & event timeline | yes | change_request ingestion, LLM-free event evidence, inventory-diff detector, preceded_by diagnosis window |
| 5 Telemetry index | derived subset | **monitoring_sources** entity fact stamped from alert-shaped evidence (index_monitoring_sources; reconciled each sweep — stale coverage drops off instead of unioning forever); full monitor-config map needs Datadog/Splunk config-API connectors |
| 6 Knowledge & history | strongest — yes | KB chunks + applicability, playbooks, episodes/patterns, issue/error signatures, fix_patterns with success counters (= fingerprints with success rates) |
| 7 Org metadata | partial | assigned_to_group, criticality/support_group facts; missing on-call/SLA/change-freeze (sources) |

Mechanics: provenance+timestamp+confidence per edge ✓; temporal as_of ✓;
uncertainty-first ✓; self-healing = F1 decision write-back +
`propose_dependency` MAF tool (agent-discovered topology enters as a
**reviewable proposal**, invisible to maf.v1 until promoted — the
governance line the blueprint's §1.5 mechanic implies).

Correlation primitives: change correlation ✓ · cohort analysis ✓
(`get_cohort_shared_attributes` service + MAF tool, ≥60% coverage on ≥3
CIs, empty-not-stretched) · blast radius ✓ · fingerprint-with-success ✓
· rule-out substrate ✓ · temporal clustering partial (alert rollups) ·
anomaly baselining partial (evidence baselines; per-CI metric baselines
stay in monitoring by the live-state boundary).

Remaining gaps (all connector-shaped): directory/HR feeds, monitoring
config APIs, cross-source CMDB validation, SLA/on-call/change-calendar
metadata, APM topology source.
