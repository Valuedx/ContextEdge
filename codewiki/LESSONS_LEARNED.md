# Lessons learned (plain English)

What two intensive days of building, measuring, and breaking things
taught us. Written for someone new to the project: each lesson says
what happened, why it matters, and what we now do about it. Dates are
2026-08-06/07; the commits and measurements behind every claim are in
the git history and `codewiki/18`.

## 1. Measure before you optimize — the numbers keep surprising you

We assumed capping the model's "thinking" would make calls faster.
Measurement said: capping the episode prompt changed *what it produced*
(the same evidence split into a different number of episodes), and
Vertex latency varies 3–4× at identical token counts anyway. So
thinking caps are a **cost** lever, not a speed lever, and only one
prompt (relevance) has one. Every prompt/projection change now needs a
before/after A/B on real data — twice this week the A/B **failed** and
stopped a bad change (episode caps; claims-in-the-gate-call moved half
the borderline verdicts).

## 2. Concurrency finds the bugs that were always there

Switching workers from one-task-at-a-time to 8 threads exposed, within
hours: an asyncio lock bound to the wrong event loop (499/499 task
failures), and episode reconstruction racing itself (8 identical
episodes minted in 46 seconds). Both bugs existed before; they just
never had two hands on the same data at once. The fixes are the same
pattern each time: **per-loop or per-resource locks** (Postgres
advisory locks, like syncs always had), and losers *skip cheaply*
instead of duplicating expensive work.

## 3. Head-truncation loses knowledge silently

Every `text[:2000]` takes the FIRST 2000 characters. In an email
thread the newest reply is greetings and scheduling — the fix is at
the bottom. A real 78-message thread was classified "not relevant"
this way and a complete, reusable resolution never entered the graph.
Salience-aware slicing (skip boilerplate, keep substance) flipped all
five giant threads to "operational" at 0.90+ confidence. Lesson: when
you must cut text, cut the *right* text.

## 4. Guardrails work — and they will fire on you

The daily token budget (built as a safety net) stopped our own bulk
ingest at exactly 2M tokens, freezing the pipeline for 9.5 minutes
until we raised it. That is the guardrail *working*. The operational
lesson: provision the tenant's budget row BEFORE a bulk backfill
(RUNBOOK has the sizing rule: ~100k tokens per thread-heavy ticket,
cold start).

## 5. Cheap work must never queue behind expensive work

500 quick classification calls (~2.5s each) waited ~40 minutes behind
20–60s extraction tasks in the same queue. One routing line fixed it
(classification now has its own lane). General rule: FIFO queues need
fast lanes, or bulk operations starve everything small.

## 6. Cold start is a one-time tax, and the system pays it down itself

The isolated 84-ticket run made 445 identity-adjudication LLM calls —
because the database started empty. But it banked 1,029 aliases while
running, and by the end roughly half of all identity mentions resolved
with **zero** LLM calls. Re-running the same corpus would skip most of
that cost. Judge cold-start economics separately from steady-state.

## 7. Silent filter failures ingest 3× what you asked for

We asked Zoho for ~29 closed tickets and got 84 — the filter used the
wrong config key (ServiceNow's `table_filters` instead of Zoho's
`module_filters`) and nothing errored. Connectors now verify rows
client-side after fetching, so an ignored filter can never silently
sync a whole window again. Lesson: an API that ignores unknown
parameters needs a client-side backstop.

## 8. Most of what a design asks for is already built — and invisible

Again and again the gap wasn't capability, it was **visibility**:
causal edges written since launch but absent from the agent's
allowlist; signature tables populated but not seedable; a CMDB
topology cache nobody could traverse. Before building anything, check
whether the data already exists and just can't be *seen*.

## 9. Local models: integration is trivial, hardware is the question

Serving our fine-tuned GGUFs took minutes (llama-server speaks
OpenAI; LiteLLM routes per task). But on a CPU-only desktop a capable
4B model is ~17s/call vs Vertex's 2.4s — slower for equal-or-worse
quality. On the GPU the models were trained on it would be
competitive. Feasibility lives in the hardware column.

## 10. Duplicate schedulers double your pipeline

Two Celery beats were running (one from a stale launch); every
scheduled task fired twice for days. Check process inventories, not
just logs: `ps` told us what the logs never did.
