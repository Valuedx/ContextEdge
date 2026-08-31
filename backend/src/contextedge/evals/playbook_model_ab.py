"""A/B: two models on real playbook generation, same inputs, model swapped.

Runs the REAL generator — prompt, source-ref validation, grounding
classification — on the same pattern inputs with only the model swapped, so
the comparison measures the model and nothing else. Nothing is persisted:
this decides ``playbook_model``, it does not ship playbooks.

Sample: patterns spanning episode counts and confidence bands, because a
model that shines on a 5-episode pattern may pad a 1-episode one with
invented best practice — and grounding is the axis that matters most:
``grounding_status`` is structural (validated citations), so the model cannot
claim it.

Verdict on record (2026-08-17, 6 patterns from the live Zoho corpus,
snapshot in ``datasets/playbook_model_ab_2026-08-17.json``):
``vertex_ai/gemini-3.7-flash`` beat ``vertex_ai/gemini-2.5-flash`` on every
axis that matters and lost none — grounded-step share 0.70 -> 0.81 (never
worse on any pattern), steps 10.7 -> 5.8 (tighter, not thinner: refs held),
latency 25.5s -> 14.5s, rollback notes 6/6 on both. ``playbook_model``
defaults to 3.7-flash on that basis. The PATTERN lane was not measured here
and stays on 2.5-flash; re-run with ``pattern``-lane inputs before flipping
it.

Run from ``backend/``::

    python -m contextedge.evals.playbook_model_ab [model_a] [model_b]

Costs 2 x SAMPLE real playbook generations plus their knowledge-retrieval
reads. Results are written next to this module's ``datasets/`` directory,
stamped with today's date.
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

TEN = uuid.UUID("00000000-0000-0000-0000-000000000001")
SAMPLE = 6
DEFAULT_MODELS = ("vertex_ai/gemini-2.5-flash", "vertex_ai/gemini-3.7-flash")


async def build_inputs(db, pattern):
    """Assemble generator inputs exactly the way pattern_tasks does."""
    from contextedge.models.episode import Episode
    from contextedge.models.pattern import PatternEvidenceLink
    from contextedge.services.episode_service import (
        evidence_ids_for_episodes,
        playbook_episode_summaries,
    )
    from contextedge.services.knowledge_applicability_service import (
        ticket_version_custom_fields,
    )
    from contextedge.services.knowledge_retrieval_service import (
        retrieve_knowledge_for_pattern,
    )

    lr = await db.execute(
        select(PatternEvidenceLink).where(PatternEvidenceLink.pattern_id == pattern.id)
    )
    ep_ids = [ln.episode_id for ln in lr.scalars().all() if ln.episode_id]
    er = await db.execute(select(Episode).where(Episode.id.in_(ep_ids)))
    episodes = list(er.scalars().all())
    summaries = await playbook_episode_summaries(db, TEN, episodes)
    evidence_ids = await evidence_ids_for_episodes(db, TEN, ep_ids)
    version_fields = await ticket_version_custom_fields(db, TEN, evidence_ids)
    knowledge = await retrieve_knowledge_for_pattern(
        db, TEN, pattern_title=pattern.title,
        pattern_description=pattern.description, episode_summaries=summaries,
        custom_fields=version_fields or None,
    )
    return summaries, knowledge


def score(result, elapsed):
    steps = result.get("steps") or []
    grounded = [s for s in steps if s.get("grounding_status") == "grounded"]
    refs = sum(len(s.get("source_refs") or []) for s in steps)
    return {
        "steps": len(steps),
        "grounded_steps": len(grounded),
        "grounded_share": round(len(grounded) / len(steps), 2) if steps else 0,
        "source_refs": refs,
        "confidence": result.get("confidence"),
        "has_rollback": bool(result.get("rollback_notes")),
        "has_verification": bool(result.get("verification_policy")),
        "latency_s": round(elapsed, 1),
    }


async def main(models=DEFAULT_MODELS):
    from contextedge.ai.generators import playbook_generator
    from contextedge.models.pattern import Pattern

    out = (Path(__file__).parent / "datasets"
           / f"playbook_model_ab_{datetime.date.today().isoformat()}.json")
    eng = create_async_engine(settings.database_url)
    session_factory = async_sessionmaker(eng, expire_on_commit=False)

    async with session_factory() as db:
        # Spread: highest-confidence multi-episode down to singletons.
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

        results = []
        for pat in sample:
            summaries, knowledge = await build_inputs(db, pat)
            row = {"pattern": pat.title, "episodes": len(summaries)}
            for model in models:
                # MODEL_ROUTING is snapshotted at import; patch the dict itself.
                from contextedge.ai import provider
                provider.MODEL_ROUTING["playbook"] = model
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
                    row[model.split("/")[-1]] = score(result, time.time() - t0)
                    await db.commit()  # usage events persist per call
                except Exception as exc:
                    row[model.split("/")[-1]] = {
                        "error": f"{type(exc).__name__}: {str(exc)[:80]}"
                    }
            results.append(row)
            labels = [m.split("/")[-1] for m in models]
            print(f"[{row['episodes']} ep] {pat.title[:52]}")
            for label in labels:
                print(f"   {label}: {row.get(label)}")
            print()

    out.write_text(json.dumps(results, indent=1))
    print(f"results -> {out}")

    def agg(label):
        rows = [r[label] for r in results
                if "error" not in r.get(label, {"error": 1})]
        if not rows:
            return {}
        return {
            "avg_steps": round(statistics.mean(r["steps"] for r in rows), 1),
            "avg_grounded_share": round(
                statistics.mean(r["grounded_share"] for r in rows), 2),
            "total_refs": sum(r["source_refs"] for r in rows),
            "avg_latency": round(statistics.mean(r["latency_s"] for r in rows), 1),
            "rollback": sum(r["has_rollback"] for r in rows),
            "verification": sum(r["has_verification"] for r in rows),
            "n": len(rows),
        }

    print("=" * 60)
    print("AGGREGATE")
    for model in models:
        label = model.split("/")[-1]
        print(f"  {label}: {agg(label)}")
    await eng.dispose()


if __name__ == "__main__":
    chosen = tuple(sys.argv[1:3]) if len(sys.argv) >= 3 else DEFAULT_MODELS
    asyncio.run(main(chosen))
