# Offline evals

Decides whether a prompt version or a model change is actually better,
instead of arguing about it.

## Why

Two questions kept coming up with nothing to settle them:

- **Is identity prompt v3 better than v2?** v3 removed junk reliably, but
  entity counts swung between runs. On six unlabelled documents there is
  no way to tell "correctly stopped extracting rubbish" from "started
  dropping real entities" — the count falls either way.
- **Would a different model help?** Most of what was broken turned out to
  be SQL, regexes and token budgets, where the model is irrelevant. But
  extraction stability is one place a stronger model plausibly helps.

Both need the same thing: labelled cases and a repeatable score.

## Running

```bash
cd backend
python -c "
import asyncio, sys; sys.path.insert(0,'src')
from contextedge.evals.extraction_eval import load_cases, run_variant

async def main():
    cases = load_cases()
    for version in ('v2', 'v3'):
        result = await run_variant(cases, prompt_version=version, samples=3)
        print(result.render())

asyncio.run(main())
"
```

To compare **models** rather than prompts, pass `model=`:

```python
await run_variant(cases, prompt_version='v3', model='vertex_ai/gemini-2.5-flash')
await run_variant(cases, prompt_version='v3', model='<other model>')
```

Costs one call per case per sample. 19 cases × 3 samples ≈ 57 calls per
variant.

## What it measures

| Metric | Needs labels | What it catches |
|---|---|---|
| `junk` | no | Names wrong in any corpus: `.exe`, error codes, MIME types, ticket ids, ports |
| `missing` | yes | Entities a correct extraction must not omit |
| `forbidden` | yes | Things it must never emit |
| `stability` | no | How much the answer moves across repeated runs of the same case |

Three families because no one of them decides anything alone. A prompt
returning nothing scores perfectly on `junk`. A prompt returning
everything scores perfectly on `missing`. And a prompt can be right on
average while being unreliable per document — which matters, because the
graph is built one document at a time.

`stability` is the one that was missing, and it needs no labels, so it
works on any corpus you point it at.

## The result that settled v3

19 labelled cases, 3 samples each:

```
v2   entities= 74  junk=  7 (9.5%)  missing=0  forbidden=23  stability=0.96
v3   entities= 42  junk=  0 (0.0%)  missing=0  forbidden= 3  stability=1.00
```

`missing = 0` is what decided it. v3 dropped no labelled entity at all —
the entity-count fall was the junk going away, not recall loss. And v3
turned out to be *more* stable than v2, not less; the variance that had
looked like a risk was an artefact of counting unlabelled output.

## Adding cases

One JSON object per line in `datasets/entity_extraction.jsonl`; `//`
lines are ignored.

```json
{"case_id": "unique-slug",
 "text": "verbatim evidence text",
 "must_include": ["things a correct answer cannot omit"],
 "must_exclude": ["things it must never emit"],
 "note": "why this case exists"}
```

Both label lists are **partial on purpose**. Enumerating every entity in
a 10k-character article is not sustainable, and a set nobody maintains is
worse than a small one that is trusted. So `missing` means "of the things
we insisted on", not "of everything present".

Use text from real ingested evidence. The cases that matter are the ones
your corpus actually contains — every case here corresponds to something
a prompt version got right or wrong on live data.

**Add a case whenever a prompt change surprises you.** That is what makes
the next change decidable. Cases where the correct answer is *no
entities* are especially valuable: a set where every case expects
entities cannot detect over-extraction, which is the failure that filled
this graph with ticket numbers.

## Playbook model A/B

`playbook_model_ab.py` answers the model question for the playbook lane
the same way: the REAL generator on the same pattern inputs with only
the model swapped. The deciding metric is `grounded_share`, because
`grounding_status` comes from validated citations — the model cannot
claim it. Live patterns, not stored cases, so run it against a graph
that has some (the 2026-08-17 run used 6 spanning multi-episode to
singleton).

```bash
cd backend
python -m contextedge.evals.playbook_model_ab \
  vertex_ai/gemini-2.5-flash vertex_ai/gemini-3.7-flash
```

The 2026-08-17 verdict (snapshot in
`datasets/playbook_model_ab_2026-08-17.json`): 3.7-flash won — grounded
share 0.70 → 0.81, steps halved with refs held, latency halved — and
`playbook_model` now defaults to it. Full write-up in
`codewiki/18-cost-observability-and-containment.md`.

## Known gap

There is no applicability harness yet. `knowledge_applicability` has the
same shape of question (does a facet fire correctly, and consistently)
and would fit the same three metric families.
