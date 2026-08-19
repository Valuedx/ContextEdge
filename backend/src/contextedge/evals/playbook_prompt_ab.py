"""A/B: two PROMPT VERSIONS on real playbook generation, same inputs, same model.

Sibling of ``playbook_model_ab`` with the other variable isolated: the model
stays on the lane default and only the registry default for the ``playbook``
prompt is swapped per arm. Nothing is persisted — this decides the prompt
default, it does not ship playbooks.

Structural axes reuse ``playbook_model_ab.score`` (steps, grounded share,
refs, rollback, latency). Three axes structure cannot see — causal
sequencing, redundant steps, language quality — are scored by a blind LLM
judge on the classification lane: it sees one generated playbook at a time
and never which prompt produced it.

Verdict on record (2026-08-19, ``v5`` vs ``v6``, 6 patterns, snapshot in
``datasets/playbook_prompt_ab_2026-08-19.json``): **v6 wins and is the
default** — steps 6.3 -> 5.5 at 62 -> 61 refs (tighter, not thinner),
grounded share 0.79 -> 0.94, language grade 4.67 -> 5.0, rollback 6/6 and
latency 16.6s on both.

Two negative results worth not re-litigating:

1. **The judge's ``logic_flaws`` count is too noisy to decide on.** It read
   4 (v5) vs 6 (v6), concentrated entirely in one pattern; re-running that
   same pattern reversed it to 3 (v5) vs 0 (v6). Both arms regenerate per
   run, so a single-pattern flaw delta is sampling noise, not signal.
2. **Prompting does not fix branch validity.** A deterministic audit over
   8 patterns (dead branches, dangling targets, self-loops, unreachable
   steps) found both versions clean on 5 of 8, with v6 emitting more
   defect occurrences (6 vs 3). The same audit over the 190 live playbooks
   found 20 defective — 39% of the 51 that branch at all. This is enforced
   in code by ``playbook_generator.sanitize_branching_logic``, not by
   prompt wording, and no future prompt version should claim credit for it.

Run from ``backend/``::

    python -m contextedge.evals.playbook_prompt_ab [version_a] [version_b]

Costs 2 x SAMPLE real generations plus SAMPLE x 2 judge calls.
"""

import asyncio
import datetime
import json
import statistics
import sys
import time
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from contextedge.config import settings
from contextedge.evals.playbook_model_ab import SAMPLE, build_inputs, score

TEN = uuid.UUID("00000000-0000-0000-0000-000000000001")
DEFAULT_VERSIONS = ("v5", "v6")

_JUDGE_SYSTEM = """You review ONE machine-generated IT operations playbook.
Judge the procedure as written; do not rewrite it. Respond in JSON:
{
  "sequencing_violations": ["step N before step M but depends on it", ...],
  "redundant_steps": [step orders deletable or mergeable without losing coverage or safety],
  "logic_flaws": ["short description", ...],
  "language_grade": 1-5,
  "language_notes": "one sentence"
}
language_grade: 5 = plain, friendly, imperative, concrete; 1 = vague
corporate filler. A branch target that does not exist is a logic flaw."""


async def judge(result, db):
    from contextedge.ai.provider import llm_complete_json

    doc = {
        "steps": [
            {k: s.get(k) for k in ("order", "type", "text", "expected_outcome", "on_failure")}
            for s in (result.get("steps") or [])
            if isinstance(s, dict)
        ],
        "branching_logic": result.get("branching_logic"),
        "rollback_notes": result.get("rollback_notes"),
    }
    verdict = await llm_complete_json(
        json.dumps(doc, indent=1),
        task="classification",
        system_prompt=_JUDGE_SYSTEM,
        tenant_id=TEN,
        db=db,
        prompt_name="playbook_prompt_ab_judge",
        prompt_version="v1",
    )
    if not isinstance(verdict, dict):
        return {"error": "judge_unparseable"}
    return {
        "sequencing_violations": len(verdict.get("sequencing_violations") or []),
        "redundant_steps": len(verdict.get("redundant_steps") or []),
        "logic_flaws": len(verdict.get("logic_flaws") or []),
        "language_grade": verdict.get("language_grade"),
        "language_notes": verdict.get("language_notes"),
    }


async def main(versions=DEFAULT_VERSIONS):
    from contextedge.ai import prompts as prompt_registry
    from contextedge.ai.generators import playbook_generator
    from contextedge.models.pattern import Pattern

    out = (Path(__file__).parent / "datasets"
           / f"playbook_prompt_ab_{datetime.date.today().isoformat()}.json")
    eng = create_async_engine(settings.database_url)
    session_factory = async_sessionmaker(eng, expire_on_commit=False)

    async with session_factory() as db:
        pr = await db.execute(
            select(Pattern).where(Pattern.tenant_id == TEN)
            .order_by(Pattern.episode_count.desc(), Pattern.confidence.desc())
        )
        pats = list(pr.scalars().all())
        multi = [p for p in pats if (p.episode_count or 0) >= 3][:4]
        mid = [p for p in pats if (p.episode_count or 0) == 2][:3]
        single = [p for p in pats if (p.episode_count or 0) <= 1][:3]
        sample = (multi + mid + single)[:SAMPLE]
        print(f"sample: {len(sample)} patterns "
              f"({len(multi)} multi, {len(mid)} pair, {len(single)} single)\n")

        baseline_default = prompt_registry._DEFAULTS.get("playbook")
        results = []
        try:
            for pat in sample:
                summaries, knowledge = await build_inputs(db, pat)
                row = {"pattern": pat.title, "episodes": len(summaries)}
                for version in versions:
                    prompt_registry._DEFAULTS["playbook"] = version
                    t0 = time.time()
                    try:
                        result = await playbook_generator.generate_playbook_candidate(
                            pattern_title=pat.title,
                            pattern_description=pat.description,
                            episode_count=len(summaries),
                            episode_summaries=summaries,
                            negative_knowledge=[],
                            knowledge_sources=knowledge,
                            tenant_id=TEN,
                            db=db,
                        )
                        row[version] = {
                            **score(result, time.time() - t0),
                            "judge": await judge(result, db),
                        }
                        await db.commit()  # usage events persist per call
                    except Exception as exc:
                        row[version] = {"error": f"{type(exc).__name__}: {str(exc)[:80]}"}
                results.append(row)
                print(f"[{row['episodes']} ep] {pat.title[:52]}")
                for version in versions:
                    print(f"   {version}: {row.get(version)}")
                print()
        finally:
            prompt_registry._DEFAULTS["playbook"] = baseline_default

    out.write_text(json.dumps(results, indent=1))
    print(f"results -> {out}")

    def agg(version):
        rows = [r[version] for r in results
                if "error" not in r.get(version, {"error": 1})]
        if not rows:
            return {}
        judged = [r["judge"] for r in rows if "error" not in r.get("judge", {})]
        return {
            "avg_steps": round(statistics.mean(r["steps"] for r in rows), 1),
            "avg_grounded_share": round(
                statistics.mean(r["grounded_share"] for r in rows), 2),
            "total_refs": sum(r["source_refs"] for r in rows),
            "rollback": sum(r["has_rollback"] for r in rows),
            "avg_latency": round(statistics.mean(r["latency_s"] for r in rows), 1),
            "sequencing_violations": sum(j["sequencing_violations"] for j in judged),
            "redundant_steps": sum(j["redundant_steps"] for j in judged),
            "logic_flaws": sum(j["logic_flaws"] for j in judged),
            "avg_language_grade": round(statistics.mean(
                j["language_grade"] for j in judged
                if isinstance(j.get("language_grade"), (int, float))), 2),
            "n": len(rows),
        }

    print("=" * 60)
    print("AGGREGATE")
    for version in versions:
        print(f"  {version}: {agg(version)}")
    await eng.dispose()


if __name__ == "__main__":
    chosen = tuple(sys.argv[1:3]) if len(sys.argv) >= 3 else DEFAULT_VERSIONS
    asyncio.run(main(chosen))
