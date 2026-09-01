"""Batch-assess every playbook in a tenant and export a triage report.

Shadow mode only — never blocks publication. Use after seeding policy/ontology
or before enabling PLAYBOOK_QUALITY_MODE=enforcing.

    python backend/scripts/batch_assess_playbook_corpus.py --tenant <uuid>
    python backend/scripts/batch_assess_playbook_corpus.py --tenant <uuid> --output report.json
    python backend/scripts/batch_assess_playbook_corpus.py --tenant <uuid> --limit 50
    python backend/scripts/batch_assess_playbook_corpus.py --tenant <uuid> --states candidate,approved

Exit code 0 on success, 1 on fatal error.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import sqlalchemy as sa  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine  # noqa: E402

from contextedge.config import settings  # noqa: E402
from contextedge.models.playbook import Playbook  # noqa: E402
from contextedge.services.playbook_quality_service import assess_playbook  # noqa: E402


async def batch_assess(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    limit: int | None,
    lifecycle_states: list[str] | None,
) -> dict:
    q = (
        sa.select(Playbook)
        .where(Playbook.tenant_id == tenant_id)
        .order_by(Playbook.created_at.desc())
    )
    if lifecycle_states:
        q = q.where(Playbook.lifecycle_state.in_(lifecycle_states))
    if limit is not None:
        q = q.limit(limit)

    playbooks = (await db.execute(q)).scalars().all()
    state_counts: Counter[str] = Counter()
    category_counts: Counter[str] = Counter()
    rows: list[dict] = []
    errors = 0
    assessed = 0

    for playbook in playbooks:
        try:
            assessment = await assess_playbook(
                db,
                playbook,
                origin="batch_corpus",
            )
            await db.flush()
            if assessment is None:
                errors += 1
                rows.append(
                    {
                        "playbook_id": str(playbook.id),
                        "title": playbook.title,
                        "lifecycle_state": playbook.lifecycle_state,
                        "overall_state": "error",
                        "error": "assessment_persist_failed",
                    }
                )
                continue
            assessed += 1
            state_counts[assessment.overall_state] += 1
            rows.append(
                {
                    "playbook_id": str(playbook.id),
                    "title": playbook.title,
                    "lifecycle_state": playbook.lifecycle_state,
                    "overall_state": assessment.overall_state,
                    "content_hash": assessment.content_hash[:12],
                    "dimension_states": assessment.dimension_states,
                }
            )
        except Exception as exc:  # noqa: BLE001
            errors += 1
            rows.append(
                {
                    "playbook_id": str(playbook.id),
                    "title": playbook.title,
                    "lifecycle_state": playbook.lifecycle_state,
                    "overall_state": "error",
                    "error": str(exc)[:300],
                }
            )

    await db.commit()

    return {
        "tenant_id": str(tenant_id),
        "generated_at": datetime.now(UTC).isoformat(),
        "total_playbooks": len(playbooks),
        "assessed": assessed,
        "errors": errors,
        "state_summary": dict(state_counts),
        "category_summary": dict(category_counts),
        "pass_rate": round(
            state_counts.get("pass", 0) / max(assessed, 1),
            4,
        ),
        "playbooks": rows,
    }


async def run(
    dsn: str,
    tenant_id: uuid.UUID,
    *,
    output: Path | None,
    limit: int | None,
    lifecycle_states: list[str] | None,
) -> int:
    engine = create_async_engine(dsn)
    async with AsyncSession(engine) as db:
        report = await batch_assess(
            db,
            tenant_id,
            limit=limit,
            lifecycle_states=lifecycle_states,
        )

    await engine.dispose()

    text = json.dumps(report, indent=2)
    if output:
        output.write_text(text, encoding="utf-8")
        print(f"Wrote {output}")
    else:
        print(text)

    print(
        f"\nSummary: {report['assessed']}/{report['total_playbooks']} assessed, "
        f"pass rate {report['pass_rate']:.1%}, errors {report['errors']}"
    )
    print(f"States: {report['state_summary']}")
    return 0 if report["errors"] == 0 or report["assessed"] > 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", default=settings.database_url)
    parser.add_argument("--tenant", type=uuid.UUID, required=True)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--states",
        default=None,
        help="Comma-separated lifecycle states (default: all)",
    )
    args = parser.parse_args()

    states = [s.strip() for s in args.states.split(",")] if args.states else None
    dsn = args.dsn
    if "+asyncpg" not in dsn:
        dsn = dsn.replace("postgresql://", "postgresql+asyncpg://", 1)

    return asyncio.run(
        run(
            dsn,
            args.tenant,
            output=args.output,
            limit=args.limit,
            lifecycle_states=states,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
