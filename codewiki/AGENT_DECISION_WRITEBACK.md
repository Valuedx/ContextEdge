# Agent diagnoses flow back, and cannot become their own evidence

**Status:** shipped 2026-08-21. Roadmap F1 — the item the roadmap calls its biggest structural omission.
**Companion docs:** [DIAGNOSTIC_CONTEXT](DIAGNOSTIC_CONTEXT.md), [16-decision-traces](16-decision-traces.md), [EFFICACY_AND_KNOWLEDGE_DRIFT](EFFICACY_AND_KNOWLEDGE_DRIFT.md), [KNOWN_GAPS](KNOWN_GAPS.md).

---

## Summary

Every other roadmap item makes the graph a better reference. This one makes it learn from being used.

An agent works a signature, rules out the connection-leak hypothesis on evidence, finds it was the pool size — and none of that survives the session. The next agent repeats the ruled-out hypothesis at the same cost with the same tools. F1 records the trail, and lets the next investigation inherit it.

Exercised live on the canonical incident: three hypotheses recorded (two rejected with evidence-based reasons), **invisible to the next reader while pending**, visible to a review surface, and inherited only once an outcome moved it off `pending`.

## Business picture

The half nobody writes down is the rejections. A ticket records the fix; it never records the four things checked first. So the fifth engineer to see the signature re-checks all four.

A confirmed cause is useful once. A ruled-out hypothesis is useful every time the signature recurs — and recording it with a reason turns each investigation into a shorter next one. That compounding is the difference between a system that remembers and one that learns.

## The hazard, and why this was safe to build

An agent that reads its own unreviewed conclusions as evidence launders opinion into fact and grows more confident every lap. It is the reason write-back is dangerous, and the reason it stayed unbuilt.

**The guard already existed.** `graph/agent/hydrators.py` drops any decision with `actor_type='ai'` and `status='pending'`:

> A pending AI-authored decision is an unreviewed diagnosis. It must not steer the next agent until a human review or a recorded outcome moves it past "pending" — otherwise agent output launders itself into agent input.

So a diagnosis written here is **inert on arrival**. F1 depends on that guard rather than restating it, which is worth saying out loud: *do not add a retrieval path that ignores it.*

Three places enforce it, deliberately not one:

| where | how |
| --- | --- |
| the projection | drops pending AI decisions (pre-existing) |
| `prior_hypotheses` | filters them explicitly — a different code path the projection does not cover |
| the agent's client port | exposes no `include_unreviewed` argument at all |

The third is the cheapest guarantee: an agent cannot request unreviewed conclusions because it has no way to ask.

## Walkthrough

### What gets written

The machinery already existed and nothing called it. `decision_trace_service.create_decision` takes options; `DecisionOption` carries `selected`, `rejection_reason` and `rejection_code` — which *is* "hypotheses considered, which was chosen, and why the others were not". F1 is the agent-facing shape of it, so review, audit and supersession apply to agent-authored records exactly as to human ones.

A selected hypothesis never carries a rejection reason, even if a caller supplies one: a row asserting both taken and refused is a contradiction downstream.

### What closes the loop

An **outcome**, not the passage of time. `record_diagnosis_outcome` writes a `DecisionOutcome` and moves the decision off `pending`, which is what makes it visible to the next agent. A diagnosis nobody ever confirmed or refuted stays inert forever — the correct default, because an unverified conclusion should not become the next agent's premise merely by ageing.

### The two tools, and their order

`prior_hypotheses` is registered before `record_diagnosis`. The order is the hint: inheriting what was already ruled out is the half that saves work, and the half an agent will skip if it reads the write tool first. The tool description says so explicitly, and also tells the agent its write is inert until reviewed — the tool text is the only place an agent learns that writing back is not publishing.

## Decisions

**Write-back goes through the existing decision machinery, not a parallel store.**
*Why:* governance already applies there — review, audit, supersession, the projection guard. A separate agent-diagnosis table would have needed all of it rebuilt, and would have been reviewed by nobody.
*Tradeoff:* agent diagnoses share a table with human decisions, so any query that forgets `actor_type` mixes them. Every query here filters; nothing forces the next one to.

**Rejections require a reason.**
*Why:* a hypothesis dropped without one teaches nothing and is indistinguishable from one never seriously considered.
*Tradeoff:* nothing validates that the reason is *good*. "Seemed unlikely" is accepted by the schema and useless to the reader; only the tool description discourages it.

**An outcome, not age, promotes a diagnosis.**
*Why:* time is not verification.
*Tradeoff:* in a deployment where nobody records outcomes, F1 writes a great deal and returns nothing forever. That is the honest failure — silence rather than unearned confidence — but it will look like the feature is broken.

**The agent's port has no `include_unreviewed` argument.**
*Why:* a guarantee an agent cannot reach is stronger than one it is asked to respect.
*Tradeoff:* an agent genuinely needing to see pending work — a review-assistant agent, say — cannot, and would need a different port.

## Code map

| Path | Role |
| --- | --- |
| `services/agent_diagnosis_service.py` | record, outcome, `prior_hypotheses` |
| `integrations/maf/client.py::InProcessDiagnosisClient` | the agent's port, deliberately narrow |
| `integrations/maf/tools.py::DiagnosisTools` | `prior_hypotheses`, `record_diagnosis` |
| `api/v1/decisions.py::prior_hypotheses_endpoint` | `GET /decisions/prior-hypotheses` |
| `graph/agent/hydrators.py` | the pre-existing guard F1 relies on |
| `tests/test_agent_diagnosis_writeback.py` | both ends of the containment |

## Acme VPN incident (this layer)

The diagnosis recorded against the VPN situation reads:

```text
x  Leaf certificate expired
   Leaf validity runs to 2027-03; checked on the gateway.
x  RADIUS backend unreachable
   radius-auth-01 answered test binds throughout the window.
>  Intermediate CA rejected by hardened chain validation   (confidence 0.86)
```

While pending, the next agent saw none of it. After the fix was recorded as successful, the next agent inherits all three — including the two dead ends, which are the ones that save it a tool call each.

Every earlier article describes what its layer contributes to this incident. This is the layer where the incident starts contributing back.

## References

- Roadmap F1: [INCIDENT_DIAGNOSIS_ROADMAP](INCIDENT_DIAGNOSIS_ROADMAP.md)
- The decision machinery this uses rather than replaces: [16-decision-traces](16-decision-traces.md)
- The guard, in context: `graph/agent/hydrators.py`
