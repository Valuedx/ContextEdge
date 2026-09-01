# Playbook Clarification Loop — Requirement and Design

**Version:** 1.0.0
**Status:** Implemented (Phase C1, shadow-safe)
**Depends on:** `docs/PLAYBOOK_QUALITY_PERMANENT_FIX_PLAN.md` v4.0.0 (Phases 0–4)
**Migrations:** `0095_playbook_clarification`, `0096_clarification_regeneration`

---

## 1. The requirement

> If playbook quality requires more details to improve, ask relevant questions. If the
> required data is already available in the KB, use it. If the data is not available
> anywhere, ask questions and collect the answers. Fill in all required questions and
> update the playbook based on the existing/old playbook. Repeat this process as many
> times as required. Ask questions whenever additional information is needed. Once
> everything is complete, submit the playbook. Mandatory questions must be answered,
> while optional questions can be skipped. The questions must be AI-generated
> dynamically based on the specific requirements and should not be static or hardcoded.

Restated as acceptance criteria:

| # | Criterion | Where it is enforced |
|---|---|---|
| R1 | A quality defect that a human could fix by supplying a fact becomes a question. | `quality/clarification/gaps.py` |
| R2 | A defect that a human cannot fix by supplying a fact does **not** become a question. | `ANSWERABLE_CATEGORIES` allow-list |
| R3 | The KB is consulted before the human is. | `quality/clarification/kb_resolution.py`, called before question generation |
| R4 | Question wording is model-generated per playbook, never a template. | `ai/prompts/clarification.py`, `ai/generators/clarification_generator.py` |
| R5 | The *set* of questions is derived from real defects, not invented by the model. | Generator drops any question whose `gap_key` was not supplied |
| R6 | Mandatory questions must be answered; optional ones may be skipped. | `obligation` column + `mandatory_outstanding()` |
| R7 | The updated playbook is derived from the existing one, not regenerated from scratch. | `playbook_revision` prompt takes the current playbook as input; new version carries `derived_from_version_id` |
| R8 | The loop repeats until nothing is left to ask. | Round state machine |
| R9 | The loop terminates. | Answer attestation + bounded rounds — §7 |
| R10 | When everything is complete, the playbook can be submitted. | `submission_readiness()` |

---

## 2. Why this is not just "prompt the model harder"

The obvious implementation is to notice a low-quality playbook and ask the model to
improve it. That fails for the reason the whole quality plan exists: **the model does
not have the missing information.** Asking it to fill a gap it cannot fill produces
plausible padding, which is exactly the defect class the AutomationEdge review rejected
28 playbooks for.

So the loop's first move is not generation. It is triage of *where the missing fact
could come from*:

```
gap detected
   │
   ├─ already answered in the playbook or contract? ──► resolved_from_context (free)
   │
   ├─ answerable from retrieved knowledge?          ──► resolved_from_kb (cheap, cited)
   │
   └─ nowhere                                       ──► ask a human (expensive, authoritative)
```

Only the third branch spends a person's attention, and only the third branch produces a
question. That ordering is R3, and it is also the difference between a loop support
will use and one they will switch off in a week.

---

## 3. Gaps

### 3.1 What a gap is

A **gap** is one specific missing fact that keeps this playbook from being right. It has
an origin, a target, and a claim:

```python
InformationGap(
    gap_key="7f3c…",                       # stable identity, see §3.3
    kind="missing_contract_obligation",
    origin="finding",                      # finding | contract | gate | structure
    target_kind="playbook",                # playbook | field | step
    target_ref=None,                       # step_id or field name
    claim="Restart the AE Server service after applying the patch",
    severity="major",
    blocking=True,
)
```

### 3.2 Where gaps come from

Nothing here is a new inference. Every gap is a re-reading of something the quality
system already computed and already persists.

| Origin | Source | Produces |
|---|---|---|
| `finding` | Current assessment's findings whose category is in `ANSWERABLE_CATEGORIES` | One gap per finding |
| `contract` | `contract.unresolved_requirements` | One gap per requirement |
| `contract` | `contract.source_conflicts` | One adjudication gap per conflict |
| `gate` | `evidence_refs.quality_contract.gate.outcome ∈ {requires_additional_evidence, requires_conflict_adjudication, requires_pattern_split}` | One gap for the gate |
| `structure` | Empty procedure, missing rollback notes on a playbook whose contract has rollback obligations | One field gap |

### 3.3 `gap_key` — the thing that makes a *loop* possible

```
gap_key = sha256(f"{kind}|{target_kind}|{target_ref}|{normalized_claim}")[:32]
```

Round 2 recomputes gaps from scratch against the new content. Without a stable key,
every round would re-ask everything a reviewer already answered, and "repeat as many
times as required" would mean "repeat forever". With it:

- a gap that survives into the next round keeps its answer (`answer_source="carried"`);
- a gap that disappears is simply absent — the answer did its job;
- a genuinely new gap gets a new key and is a genuinely new question.

`normalized_claim` is lowercased, whitespace-collapsed and truncated, so a claim
re-worded by the model between rounds still hashes to the same gap. Getting this wrong
in either direction is the main failure mode of the whole feature: too strict and the
loop never converges, too loose and two different defects share one answer.

### 3.4 Which findings are answerable — and which are not

`ANSWERABLE_CATEGORIES` is an explicit allow-list, not a deny-list, because the default
must be "do not bother a human".

**Answerable** (a person knows the fact):

`missing_contract_obligation`, `missing_verification`, `missing_rollback`,
`insufficient_detail`, `unsupported_claim`, `unsupported_specificity`,
`contradicted_claim`, `subject_overbroad`, `subject_multiple_subjects`,
`subject_step_mismatch`, `policy_unmet_condition`, `policy_discouraged_action`,
`empty_procedure`, `missing_required_field`, `evidence_insufficient`,
`citation_unresolvable`, `stale_grounding`, `terminology_noncanonical`,
`wrong_artifact_type`.

**Not answerable** (no answer a human types can fix it):

- `validator_not_implemented`, `validator_error` — a defect in *us*, not in the content.
  Asking a reviewer about it is asking them to apologise for our backlog.
- `invalid_structure`, `duplicate_step_identity`, `unreachable_step`,
  `unresolvable_branch` — mechanical defects with a mechanical repair. The generator
  already sanitizes branching; a question here would ask a human to do arithmetic.
- `redundant_step`, `no_utility_step`, `oversized_artifact` — reductions, not additions.
  The fix is to delete, which the reviewer can already do in the editor.
- `duplicate_artifact` — a decision about two playbooks, not a fact about one.

### 3.5 Mandatory vs optional

The model proposes; **policy decides**:

```
blocking finding (critical or major)  ─► mandatory, always
gate outcome in BLOCKING_OUTCOMES     ─► mandatory, always
everything else                       ─► the model's proposal, defaulting to optional
```

A model that can mark its own blockers optional makes the distinction decorative. The
override runs after generation and is not negotiable.

---

## 4. KB-first resolution

For each gap, in order, stopping at the first hit:

1. **A person already answered this exact gap.** Looked up by `gap_key` in
   `contract.human_attested_answers` — an exact keyed lookup, never a lexical match.
   This branch is checked first and is what makes the loop terminate; §7 explains why
   it cannot be a similarity test.

2. **Context resolution** — is the fact already in the artifact? A field gap is
   answered by that field having content (a rollback obligation described inside step 4
   does not put anything in `rollback_notes`); a required action is answered by a step
   that says it under different wording. These are gaps the detector raised because a
   validator's threshold is imperfect, and asking about them would train reviewers to
   distrust the questions.

3. **KB resolution** — `retrieve_knowledge_for_pattern` is called once per round with a
   query built from the playbook subject plus the unresolved gap claims. A hit produces
   a prefilled answer with `answer_source="kb"` and provenance
   `{evidence_id, title, section_ref, score}`.

   A KB-resolved question is still **shown** to the reviewer, prefilled and labelled.
   It does not count as outstanding, and they can overwrite it. Hiding it would mean a
   wrong retrieval silently enters the playbook as if a person had approved it.

4. **Ask** — everything left goes to the question generator.

Retrieval failure is not resolution. If retrieval raises, the gap goes to step 4 and the
round records `kb_status="retrieval_failed"`, so a round with zero KB hits because the
index was down is distinguishable from one where the KB genuinely had nothing.

### 4.1 What counts as "this answers the gap"

Steps 2 and 3 share one predicate, `kb_resolution.supports`, and it requires **all
three** of:

| Condition | Why |
|---|---|
| Polarity agrees | A sentence that *declines* the action a gap asks about matches its words perfectly and answers nothing. Same guard the grounding validator uses. |
| ≥ 2 shared tokens of length ≥ 4 | See below. |
| `combined_entailment_score` ≥ 0.45 | Token overlap or bigram similarity, so a reordered paraphrase still counts. |

The middle condition exists because the first version of this module borrowed the
completeness validator's 0.25 threshold, and a test caught what that allows:
`overlap_ratio` divides by the **shorter** token set, so the step "Apply the patch."
— three tokens, one of them "the" — scores 0.33 against a completely unrelated
obligation.

The validator can afford 0.25 because its failure mode is declining to raise a
finding. Here the failure mode is telling a reviewer their question is already
settled and dropping it, so the bar is higher and a match must share real
vocabulary rather than function words. Function words are excluded by length rather
than by a stopword list, matching the rest of `claim_match`: a curated English
vocabulary would quietly stop working on a tenant's own jargon.

---

## 5. Dynamic question generation (R4, R5)

### 5.1 Contract with the model

**Input:** the playbook (title, description, step titles), the quality contract, the
tenant's ontology terms, and the unresolved gaps — each with its `gap_key`, kind,
claim, target and the KB search outcome for it.

**Output:** a JSON array; one object per supplied `gap_key`:

```json
{
  "gap_key": "7f3c…",
  "question": "After applying the patch, which service must be restarted, and in what order relative to the Bot Manager?",
  "why_it_matters": "The KB requires a restart but no supplied source names which service or the ordering.",
  "obligation": "mandatory",
  "answer_kind": "text",
  "choices": [],
  "expected_format": "Service name(s) and ordering, e.g. \"AE Server, then Bot Manager\"",
  "applies_to": {"target_kind": "step", "target_ref": "s3"}
}
```

### 5.2 The three rules that keep it honest

1. **One question per supplied key, no others.** A returned object whose `gap_key` was
   not supplied is dropped and counted. Without this the model writes a pleasant
   interview about a playbook it finds interesting rather than about what is wrong.
2. **No invented facts in the question.** The question may only reference the gap's own
   claim, the playbook's own text, and the tenant's ontology terms. A question that
   embeds an assumed answer ("Should you restart AE Server 8.2.3 or 8.3?") is how a
   clarification loop becomes a leading-question machine.
3. **Tenant vocabulary comes from the ontology, never from the code.** The product name
   is `active_product_label(tenant)`, absent for a tenant that has not named one. No
   product string appears anywhere in this feature's source.

### 5.3 Failure is a state, not an exception

If generation fails or returns nothing usable, the round is still persisted, with
`status="open"` and zero questions, and the failure recorded. The reviewer sees "we
could not compose the questions", which is true, rather than an empty panel, which
reads as "nothing to ask".

---

## 6. Round state machine

```
                    open ──answer all mandatory──► answered ──apply──► applied
                     │                                │                   │
                     │                                │            reassess & recompute gaps
                     │                                │                   │
                     │                            (skip apply)     ┌──────┴───────┐
                     │                                │            │              │
                     └──abandon──► abandoned          └────────────► satisfied   open(n+1)
                                                                   (no gaps left)  │
                                                                                   │
                                                              round_number > max ──► exhausted
```

| Status | Meaning |
|---|---|
| `open` | Questions are outstanding. |
| `answered` | Every mandatory question has an answer; optional ones may be skipped. |
| `applied` | The answers were folded into a new derived draft version and re-assessed. |
| `satisfied` | Re-assessment found no answerable gaps. This is the terminal success state. |
| `exhausted` | `max_rounds` reached with gaps remaining. Terminal; needs a human decision. |
| `abandoned` | A reviewer closed the round without applying it. |

Exactly one non-terminal round per playbook at a time, enforced by a partial unique
index.

---

## 7. Termination (R9)

"Repeat as many times as required" is a liveness requirement, and a loop that re-asks
what it was already told is the standard way this feature fails. Three mechanisms,
each necessary:

1. **Answers become attestations on the contract, matched by `gap_key`.** When a round
   is applied, every answer is written to
   `evidence_refs.quality_contract.snapshot.human_attested_answers` carrying the
   `gap_key` it answered. The completeness validator will happily re-raise the same
   `missing_contract_obligation` on the next pass — the step wording may genuinely still
   not match — and the detector will mint the same `gap_key`. Resolution step 1 finds
   the attestation under that key and settles it without reaching a person.
   **This is the single most important correctness point in the feature.**

   The lookup is keyed, not lexical, and that is not an optimisation. A reviewer who
   answers *"contact platform-ops after the second failed restart"* has settled the
   obligation *"escalate if the restart fails twice"* while sharing almost no
   vocabulary with it. A similarity test would miss it, the question would be asked
   again, and the loop would not terminate. The `gap_key` is exact, and having one is
   the whole point.

   Attestations are deliberately **not** appended to `required_actions`. That was the
   first design and it is a trap: the completeness validator would then demand a step
   whose text overlaps the answer, and an answer phrased differently from the step it
   produced becomes a permanently unsatisfiable obligation — the loop's failure mode
   inverted rather than fixed.

2. **Carry-forward by `gap_key`.** A gap that legitimately survives keeps its answer
   rather than being re-asked; the reviewer sees it as already answered and can revise
   it.

3. **A bound.** `settings.playbook_clarification_max_rounds` (default 5). On the bound,
   the round is `exhausted` and the loop stops. A clarification loop that can spend an
   unbounded number of LLM calls on one playbook is a cost incident waiting for a
   corpus refresh to trigger it.

Progress argument: each applied round either resolves at least one gap (its key
disappears) or it does not. If it does, the gap set shrinks. If it does not, the round
counter still advances, and the bound terminates it. The loop therefore ends in
`satisfied`, `exhausted` or `abandoned` in at most `max_rounds` iterations.

---

## 8. Applying answers (R7)

`apply_round` does **not** regenerate the playbook from the pattern. It revises the one
that exists:

1. Build the `playbook_revision` prompt from the **current playbook JSON**, the Q&A, the
   contract obligations and the KB block.
2. Call the model with `task="playbook"` (the 16k budget — a revision returns a whole
   playbook).
3. Post-process with the *same* functions generation uses — `validate_source_refs`,
   `classify_step_grounding`, `sanitize_branching_logic`. A revision path with its own
   post-processing is how the manual generation endpoint drifted from its worker twin.
4. Create a **new draft version** via `create_playbook_version`, carrying
   `derived_from_version_id`. Published versions stay immutable; the answers are visible
   as a version diff.
5. Stamp `evidence_refs.clarification` with the round, the answers and their sources.
6. `invalidate_and_reassess(origin="clarification_apply")`.

Steps that exist only because a human answered a question are tagged
`grounding_status="human_attested"` with the answering user and round on the step, so
`classify_step_grounding` does not silently relabel a human-supplied instruction as an
unsourced best-practice guess. A reviewer must be able to tell "support told us this"
apart from "the model thought this was a good idea".

---

## 9. Submission (R10)

`submission_readiness(playbook)` returns:

```json
{
  "ready": false,
  "blocked_reasons": ["mandatory_questions_outstanding"],
  "outstanding_mandatory": 2,
  "open_round_id": "…",
  "quality": { "ready": false, "blocked_reason": "assessment_inconclusive" }
}
```

Ready requires all of: no open round with outstanding mandatory questions; the current
assessment describes the current content; and the quality gate's own `readiness.ready`.

**The service never transitions the playbook.** Consistent with shadow mode, it reports
that the artifact is ready and the human presses Submit, which calls the existing
`POST /playbooks/{id}/transition`. A system that moves playbooks forward on its own
judgement is exactly what the support organisation rejected 28 playbooks for.

---

## 10. API

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/playbooks/{id}/clarification` | Current round, questions, submission readiness. Read-only — never opens a round. |
| `POST` | `/playbooks/{id}/clarification/rounds` | Open a round: detect gaps, resolve from context and KB, generate questions. |
| `POST` | `/playbooks/{id}/clarification/answers` | Record answers or skips for the open round. |
| `POST` | `/playbooks/{id}/clarification/regenerate` | Rewrite the wording of the unanswered questions. |
| `POST` | `/playbooks/{id}/clarification/apply` | Fold the answers into a new derived draft version and re-assess. |
| `POST` | `/playbooks/{id}/clarification/abandon` | Close the round without applying it. |

`GET` being read-only matters for the same reason `GET /quality` is: opening a playbook
must not spend LLM calls, and must not change its history to record who looked.

### 10.1 Rewriting the questions

Questions can be unusable through no fault of the playbook: too vague, or the raw
validator text the generator falls back to when the model's JSON arrives truncated —
which happened on the first live run. Without a way to ask again, the only escape is
abandoning the round, which spends one of the loop's five on a defect in *our* output.

So a rewrite is its own action, and deliberately not a re-roll:

- **Answered, skipped and KB-resolved questions are untouched.** Rewriting the text of
  a question somebody already answered orphans the answer — it becomes an answer to a
  question that was never asked.
- **Gaps are not re-detected.** The round's gap set is fixed at open time; recomputing
  it would let the set shift underneath answers already given, and would turn a rewrite
  into a new round without the round counter noticing. The gaps are rebuilt from the
  stored question rows, which reproduces each `gap_key` and its blocking status exactly
  — that last part is what keeps a mandatory question mandatory across a rewrite.
- **The KB pass is not re-run.** These gaps already survived it.
- **The model is shown what it said last time, and the reviewer's note.** At temperature
  0 the same inputs give the same output, so without the rejected wording in the prompt
  a "rewrite" returns the question the reviewer just refused. The note ("too vague — ask
  about the ordering") is the one place in this prompt where text not derived from the
  sources is allowed to steer it; it still does not license inventing a product,
  component or version.
- **Bounded at 3 per round** (`MAX_QUESTION_REGENERATIONS`, counted in
  `playbook_clarification_rounds.regeneration_count`, migration 0096). Each rewrite is a
  generation call, and a button with no counter behind it is an unbounded spend control
  shaped like an affordance. Past the bound the panel stops offering it rather than
  letting the request 409 — by the third attempt the problem is not the wording.

The rewrite block is appended to the user message rather than added as a template slot,
so prompt v1 stays byte-identical on the ordinary path and its version attribution keeps
meaning something. Same pattern the playbook generator uses for the quality-contract
block.

---

## 11. What this deliberately does not do

- **It does not block anything.** No gate is added; `evaluation_mode` stays `shadow`.
- **It does not run at generation time.** Generation always produces a draft; the loop
  operates on the draft. A generation path that can halt waiting for a human turns a
  422-playbook corpus refresh into 422 stalled jobs.
- **It does not auto-submit.** §9.
- **It does not hardcode any product, component or question text.** §5.2 rule 3.

---

## 12. Open items for the owning team

1. **Question fatigue is the real risk.** A corpus-wide open of rounds on 422 playbooks
   would generate thousands of questions nobody answers, and the panel becomes noise.
   Rounds are opened per playbook, on demand, by a reviewer — there is deliberately no
   bulk-open endpoint. Add one only with a per-tenant cap and an owner.
2. **Threshold calibration.** `SUPPORT_THRESHOLD = 0.45` and
   `MIN_SHARED_DISTINCTIVE_TOKENS = 2` are reasoned defaults, not measured ones (§4.1
   explains the reasoning). They should be calibrated against the 28 rejections and 62
   approvals once the corpus is refreshed, keeping a locked holdout. The metric to
   watch is the false-resolution rate — a gap the system said was already answered and
   was not — because that failure is silent, unlike a question asked unnecessarily.
3. **`max_rounds = 5` is a guess.** It should be set from the observed distribution of
   rounds-to-satisfied after the first month.

4. **Should an attested answer also silence the finding?** As built, it does not. The
   completeness validator keeps raising `missing_contract_obligation` after a reviewer
   answers, because the step wording genuinely still does not cover the obligation —
   the assessment stays truthful and only the *question* is settled. The alternative is
   to have the validator skip obligations a human attested, which makes the quality
   verdict itself go green on a person's say-so.

   That is a real transfer of authority and should be a deliberate decision, not a side
   effect of building the loop, which is why this ships the conservative half. Two things
   argue for revisiting it once the loop has been used: a reviewer who answers and then
   watches the same finding sit there will reasonably wonder whether they were heard, and
   the honest answer to some obligations is "that does not apply to this playbook" —
   which no amount of step wording will ever satisfy. If it is changed, the attestation
   must record who said it and in which round, and the panel must show the finding as
   *waived by a person* rather than hiding it.
