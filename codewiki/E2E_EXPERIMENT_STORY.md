# The end-to-end experiment: 84 real tickets, one night, $12.63

The story of the 2026-08-07 isolated pipeline run, told in order, with
what we observed and what each observation changed. Plain English; the
exact numbers live in the commit messages and `codewiki/18`.

## The question

*"Take the support tickets closed in the last two days, process them
through the whole pipeline in a fresh database, and tell me what it
costs and how long it takes."* Zoho Desk reported 120 tickets modified
in the window; 29 were closed/resolved-by-agent — our target.

## The setup (isolation done properly)

A brand-new database (`contextedge_e2e`), migrated and seeded from
scratch; the production Zoho source and credentials copied over; its
own Celery workers on their **own Redis database** so the two stacks
could never steal each other's tasks; and later its own API (port
8001) and UI (port 3001) so the results could be browsed without
touching production.

## What happened, hour by hour

**03:10 — dispatch.** Backfill starts. Tickets begin arriving and
auto-hydration splits each one's email thread into per-message
evidence: 27 target tickets become hundreds of items.

**Observation 1 — we got 84 tickets, not 29.** The status filter used
the wrong config key and Zoho silently ignored it, syncing the whole
modified window. (Fix shipped: client-side row verification. Lesson 7.)

**03:49 — the pipeline freezes.** The daily token budget (2M, a safety
default) trips at 2,068,652 tokens. 2,263 operations bounce off the
closed gate over 9.5 minutes until we provision a proper budget row
and re-dispatch. (The guardrail working as designed. Lesson 4.)

**04:00–05:00 — the grind.** The queue peaks at 1,584 tasks. Quick
classification calls starve behind slow episode extractions in the
same FIFO (Lesson 5 — fast lane shipped). A booster worker doubles
throughput; queue drains to zero.

**05:00 — drained.** 1,176 evidence items (84 tickets + 1,092 thread
messages), everything classified, 4,514 LLM calls, 8.56M tokens.

## The bill

**$12.63 total — about 15 cents per ticket, cold start.** But 73% of
it was episode extraction, and when we looked at WHY, we found the
run's biggest discovery: **concurrent reconstruction was minting
duplicate episodes** — 8 identical "pending review" episodes for one
conversation in 46 seconds, ~4× duplication corpus-wide. Roughly $7 of
the $12.63 was this bug. The advisory-lock fix shipped the same night
(Lesson 2), plus a gate so single-message "clusters" never pay for
episode extraction at all. A re-run would cost a fraction.

## What the graph built from one night

472 relevant items separated from 708 noise (the relevance gate paying
for itself), 465 operational summaries, 1,636 embedded chunks, 718
identities with 1,029 learned aliases (the dedup flywheel — Lesson 6),
9 error signatures, 2,101 graph edges. Health: 99.3% LLM call success,
exactly one rate-limit event at 16-wide concurrency, zero fallbacks.

## Cleaning up after the race (one-time repair)

Any deployment that ran pre-lock code (before `3f76a89`) carries the
duplicate episodes. The repair is one governed UPDATE — supersede, not
delete; keep one per fingerprint (approved first, then most evidence,
then newest):

```sql
WITH ranked AS (
    SELECT id, row_number() OVER (
        PARTITION BY tenant_id, cluster_fingerprint
        ORDER BY (reviewer_state = 'approved') DESC,
                 jsonb_array_length(evidence_ids) DESC,
                 created_at DESC
    ) AS rn
    FROM episodes
    WHERE cluster_fingerprint IS NOT NULL
      AND reviewer_state NOT IN ('superseded', 'rejected')
)
UPDATE episodes e SET reviewer_state = 'superseded'
FROM ranked r WHERE e.id = r.id AND r.rn > 1;
```

Run 2026-08-07: production superseded 114 (19 of them duplicate
*approved* episodes, so projections deduplicated too); the e2e database
superseded 851 — 84% of its episodes were race duplicates.

## Why this experiment mattered

Every fix in commit `3f76a89` — the reconstruction lock, the singleton
gate, the filter backstop, the classification fast lane, the
onboarding budget rule — came from watching this run break in ways
unit tests never would have shown. The cost of the experiment was
$12.63 and one evening; the duplicate-episode bug alone would have
cost that weekly in production.
