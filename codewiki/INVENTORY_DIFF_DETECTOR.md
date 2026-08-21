# The changes nobody records, observed by diffing state

**Status:** shipped 2026-08-21. Roadmap B3, feeding B2's event layer.
**Companion docs:** [CHANGE_CORRELATION](CHANGE_CORRELATION.md), [SERVICENOW_LIVE_VERIFICATION](SERVICENOW_LIVE_VERIFICATION.md), [INCIDENT_DIAGNOSIS_ROADMAP](INCIDENT_DIAGNOSIS_ROADMAP.md), [KNOWN_GAPS](KNOWN_GAPS.md).

---

## Summary

Most incident-causing changes never get a change record. A browser auto-updates, an agent self-patches, an OS rolls forward, a disk fills. H6 can rank the changes somebody filed; nothing could see the ones nobody did.

The detector sits where the change was **already noticed and thrown away**. `_ensure_entity` compared each incoming CI trait against the stored one, overwrote it, and said nothing. That discarded comparison is the event.

Verified live: `radius-auth-01` OS 8.6 → 8.8 with no change record produced

```text
radius-auth-01: os_version_changed 8.6 -> 8.8
```

linked to its CI, at `source_type='inventory_diff'`, `relevance_state='operational'` — and a re-warm with no further change produced no second event.

## Business picture

The canonical case in this repo is the F4 thread: a browser auto-upgraded, the web driver broke, and the fix sat undiscovered inside a 78-message email chain. No change calendar would ever have shown it, because nobody filed anything — the machine changed itself.

That class of change is invisible to every change-management process by construction, and it is a large share of what actually breaks things. The only way to know is to look at the state and notice it differs from last time.

## Walkthrough

### The detector is one hook, not a sweep

A separate scanning job would have to re-read state that had already been overwritten, and would need somewhere to store the previous snapshot — a table, a migration, a retention policy. None of that is necessary: the trait-refresh loop already holds both values at the moment of comparison.

```python
for trait, value in ref.traits.items():
    previous = getattr(existing, trait, None)
    if value and previous != value:
        setattr(existing, trait, value)
        if previous:
            transitions.append((trait, str(previous), str(value)))
```

**A first observation is not a change.** Without the `if previous:` guard every CI announces a transition the first time it is seen — noise that teaches people to ignore the feed, and a feed nobody reads is worse than none because it looks like coverage.

### Events bypass the model entirely

Per B2's design, state events are structured on arrival: no classification, no extraction, no embedding cost. `source_type='inventory_diff'`, `relevance_state='operational'`, set deterministically. That is what makes "just keep them" affordable.

They are idempotent on `(tenant, title, occurred_at)`, so re-observing the same transition cannot mint a second event.

### The defect that had to be fixed first

Traits never arrived through topology warm at all, and this is the **third** field family lost the same way.

`os` and `os_version` live on `cmdb_ci_computer`, not on the `cmdb_ci` base table the neighbourhood fetch queries. Asking the base table for a subclass column does not error — the column is simply absent from every row returned. Verified live on one sys_id: `/table/cmdb_ci` returns `name` and `sys_class_name` alone, `/table/cmdb_ci_server` returns `os: "Linux Red Hat"`, `os_version: "8.6"`.

The connector's own comment misdiagnosed it, which is why it survived: *"os/os_version exist only on computer subclasses — ServiceNow returns them empty for other classes"*. It does not return them empty; it does not return them at all, for any class.

**Dot-walking is what hid it.** An incident asking for `cmdb_ci.os` *does* get a value, because dot-walking resolves against the referenced record's real class. So traits arrived via incident enrichment and never via topology warm, and the gap looked like sparse data rather than a broken path — 16 of 140 entities had an `os_name`, and every one of them came from an incident.

The one-off service lookup added for C2 is now `SUBCLASS_DETAIL_FIELDS`, covering both families and any third.

## Decisions

**Detect at the trait-diff point rather than in a scheduled sweep.**
*Why:* the comparison already happens there with both values in hand. A sweep needs a snapshot table, a migration, and a retention policy to learn something already known for free.
*Tradeoff:* the detector only fires when something warms the CI. A CI nobody touches is never diffed, so this observes the working set rather than the estate. That matches the topology cache's own scope and is a real limit.

**Observation time, not change time.**
*Why:* nothing here knows when the browser actually upgraded — only when we next looked and found it different.
*Tradeoff:* an event's timestamp can be days after the change, which weakens the change→incident interval H6 computes from it. Using the CI's `sys_updated_on` instead would look more precise and would track the record rather than the machine.

**Query every sys_id against each subclass table rather than matching classes.**
*Why:* ServiceNow returns only rows that exist in the queried table, so the filtering is free and correct. `cmdb_ci_server`, `cmdb_ci_esx_server` and `cmdb_ci_pc_hardware` share no usable prefix, so class matching would be both fiddly and wrong.
*Tradeoff:* one extra API call per neighbourhood fetch per entry, whether or not any CI matches.

**Fail-soft, always.**
*Why:* it sits on the critical path of an entity upsert. A missed event costs a diagnostic hint; a raised exception costs the sync.
*Tradeoff:* a persistently failing event layer is invisible except in logs, and nothing counts the misses.

## Code map

| Path | Role |
| --- | --- |
| `services/servicenow_reference_service.py::_ensure_entity` | the diff point |
| `services/servicenow_reference_service.py::_emit_inventory_events` | fail-soft emission |
| `services/event_evidence_service.py::record_state_event` | B2's recorder, idempotent — existed, unused |
| `connectors/servicenow/connector.py::SUBCLASS_DETAIL_FIELDS` | the fields the base table silently drops |
| `tests/test_inventory_diff_detector.py` | first-observation rule, fail-soft, subclass fetch |

## Acme VPN incident (this layer)

The F4 case is the reason this exists: a browser auto-upgrade that broke a web driver, invisible to change management, with the fix buried in a thread nobody read.

On this deployment the same shape is now caught. `radius-auth-01` — the CI `vpn-gw-east-01` depends on — moved from OS 8.6 to 8.8 with nothing filed. The event exists, it is linked to the CI, and it sits one dependency hop from the gateway, which is exactly where H6 looks when the next VPN incident arrives.

## References

- The event layer this feeds: `services/event_evidence_service.py`, roadmap B2
- Why the base table drops subclass columns: [SERVICENOW_LIVE_VERIFICATION](SERVICENOW_LIVE_VERIFICATION.md), the C2 section
- What consumes these events: [CHANGE_CORRELATION](CHANGE_CORRELATION.md)
