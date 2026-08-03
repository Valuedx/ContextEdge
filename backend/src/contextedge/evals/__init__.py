"""Offline evaluation harnesses for prompt and model changes.

The existing ``evaluation_service`` scores retrieval and playbook ranking
against stored datasets. It has no cases for extraction or applicability,
so two decisions that came up repeatedly had nothing to decide them
with:

- Is identity prompt v3 better than v2? v3 removes junk reliably but its
  entity counts swung between runs, and six documents at one sample each
  could not separate correct exclusions from real recall loss.
- Would a different model help? Most of what was broken turned out to be
  SQL, regexes and token budgets — but extraction stability is one place
  a stronger model plausibly would.

Both questions need the same thing: a labelled set and a repeatable
score. These harnesses are deliberately offline and file-based rather
than another DB-backed feature — they are a tool for deciding what to
ship, not part of what ships.
"""
