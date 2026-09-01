"""Retire pre-quality playbooks and regenerate from patterns (v10 + contract).

Old candidates were generated before prompt v10, quality contracts, and
pregeneration gates. Re-assessing them only validates stale text. This script:

  1. Retires matching playbooks (default: ``candidate`` only — preserves audit trail)
  2. Regenerates one fresh candidate per pattern (local Celery ``.run``, no worker required)
  3. Optionally batch-assesses the new corpus

Prerequisites (run once per tenant):

    python scripts/seed_quality_policy_pack.py --tenant <uuid> --profile automationedge

Recommended .env (repo root):

    PLAYBOOK_QUALITY_MODE=shadow
    PLAYBOOK_RUNTIME_QUALITY_FILTER=true
    LLM_TASK_OUTPUT_TOKENS={"playbook":16384,"extraction":16384,"pattern":16384}

Usage:

    # Preview
    python scripts/refresh_playbook_corpus.py --tenant <uuid> --dry-run

    # Full refresh (420 candidates → retire → regenerate → assess)
    python scripts/refresh_playbook_corpus.py --tenant <uuid> --yes

    # Retire only (no LLM spend)
    python scripts/refresh_playbook_corpus.py --tenant <uuid> --retire-only --yes

    # Regenerate patterns that lack an active playbook (skip retire)
    python scripts/refresh_playbook_corpus.py --tenant <uuid> --regenerate-only --yes

Exit code 0 on success, 1 on fatal error.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import sqlalchemy as sa  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine  # noqa: E402

from contextedge.config import settings  # noqa: E402
from contextedge.models.pattern import Pattern  # noqa: E402
from contextedge.models.playbook import Playbook  # noqa: E402
from contextedge.workers.pattern_tasks import generate_playbook_candidate  # noqa: E402

DEFAULT_RETIRE_STATES = ("candidate",)


async def retire_playbooks(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    lifecycle_states: tuple[str, ...],
    dry_run: bool,
) -> list[dict]:
    q = (
        sa.select(Playbook)
        .where(
            Playbook.tenant_id == tenant_id,
            Playbook.lifecycle_state.in_(lifecycle_states),
        )
        .order_by(Playbook.created_at)
    )
    playbooks = (await db.execute(q)).scalars().all()
    retired: list[dict] = []
    now = datetime.now(UTC)

    for pb in playbooks:
        retired.append(
            {
                "playbook_id": str(pb.id),
                "title": pb.title,
                "lifecycle_state": pb.lifecycle_state,
                "pattern_id": str(pb.pattern_id) if pb.pattern_id else None,
            }
        )
        if dry_run:
            continue
        pb.lifecycle_state = "retired"
        # `pattern_id` and `title` are deliberately left alone.
        #
        # They are the only two keys that link a retired playbook to the
        # regenerated one that replaces it: the new playbook is generated from
        # the same pattern, so pattern_id is the join. The support team's 90
        # review rows point at these playbooks, and "did regeneration fix what
        # Aniket rejected?" is the one measurement this whole exercise exists
        # to enable — it needs that join for all 422, not a report of the
        # first 20.
        #
        # Nulling pattern_id was also unnecessary. The dedup guard in
        # `pattern_tasks.generate_playbook_candidate` already excludes retired
        # and deprecated rows from the "playbook already exists" check
        # (lifecycle_state.notin_), so a retired playbook keeping its
        # pattern_id cannot block regeneration.
        #
        # The title suffix cost the second join key for nothing the UI does
        # not already show: `lifecycle_state="retired"` renders as a badge and
        # the list has a lifecycle filter. It also truncated at 480 chars,
        # silently trimming any title near the 500 limit.
        pb.updated_at = now

    if not dry_run and playbooks:
        await db.commit()

    return retired


async def patterns_needing_playbooks(
    db: AsyncSession,
    tenant_id: uuid.UUID,
) -> list[Pattern]:
    active_pb = (
        sa.select(Playbook.pattern_id)
        .where(
            Playbook.tenant_id == tenant_id,
            Playbook.pattern_id.is_not(None),
            Playbook.lifecycle_state.notin_(("retired", "deprecated")),
        )
        .distinct()
    )
    q = (
        sa.select(Pattern)
        .where(
            Pattern.tenant_id == tenant_id,
            Pattern.id.notin_(active_pb),
        )
        .order_by(Pattern.created_at)
    )
    return list((await db.execute(q)).scalars().all())


def regenerate_for_patterns(
    tenant_id: uuid.UUID,
    patterns: list[Pattern],
    *,
    dry_run: bool,
    limit: int | None,
) -> list[dict]:
    rows: list[dict] = []
    todo = patterns[:limit] if limit is not None else patterns

    for index, pattern in enumerate(todo, start=1):
        if dry_run:
            rows.append(
                {
                    "pattern_id": str(pattern.id),
                    "pattern_title": pattern.title,
                    "status": "would_regenerate",
                }
            )
            continue
        try:
            # Celery task body calls asyncio.run() — must not run inside another
            # active event loop (refresh script uses asyncio for DB retire/fetch).
            result = generate_playbook_candidate.run(str(pattern.id), str(tenant_id))
        except Exception as exc:  # noqa: BLE001
            rows.append(
                {
                    "pattern_id": str(pattern.id),
                    "pattern_title": pattern.title,
                    "status": "error",
                    "error": str(exc)[:300],
                }
            )
            continue
        rows.append(
            {
                "pattern_id": str(pattern.id),
                "pattern_title": pattern.title,
                "status": result.get("status", "unknown"),
                "reason": result.get("reason"),
                "playbook_id": result.get("playbook_id"),
            }
        )
        if index % 10 == 0 or index == len(todo):
            ok = sum(1 for r in rows if r.get("status") == "ok")
            print(f"  progress {index}/{len(todo)} ok={ok}", flush=True)
    return rows


async def _prepare_refresh(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    dry_run: bool,
    retire_states: tuple[str, ...],
    retire_only: bool,
    regenerate_only: bool,
) -> tuple[list[dict], list[Pattern], dict]:
    """DB-only phase: retire and list patterns. Returns before LLM generation."""
    report: dict = {}
    retired: list[dict] = []
    patterns: list[Pattern] = []

    if not regenerate_only:
        retired = await retire_playbooks(
            db,
            tenant_id,
            lifecycle_states=retire_states,
            dry_run=dry_run,
        )
        report["retired_count"] = len(retired)
        report["retired"] = retired
        report["pattern_to_retired_playbook"] = {
            row["pattern_id"]: row["playbook_id"]
            for row in retired
            if row.get("pattern_id")
        }
        print(f"Retire: {len(retired)} playbook(s)" + (" (dry-run)" if dry_run else ""))

    if not retire_only:
        patterns = await patterns_needing_playbooks(db, tenant_id)
        report["patterns_to_regenerate"] = len(patterns)
        if dry_run and not regenerate_only and retired:
            freed = len({r["pattern_id"] for r in retired if r.get("pattern_id")})
            estimate = len(patterns) + freed
            print(
                f"Regenerate: {len(patterns)} pattern(s) now without active playbook "
                f"(~{estimate} after retire)"
            )
        else:
            print(
                f"Regenerate: {len(patterns)} pattern(s) without an active playbook"
            )

    return retired, patterns, report


async def _run_assess(
    dsn: str,
    tenant_id: uuid.UUID,
) -> dict:
    scripts_dir = Path(__file__).resolve().parent
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    from batch_assess_playbook_corpus import batch_assess  # noqa: WPS433

    assess_engine = create_async_engine(dsn)
    async with AsyncSession(assess_engine) as db:
        triage = await batch_assess(
            db,
            tenant_id,
            limit=None,
            lifecycle_states=["candidate", "approved"],
        )
    await assess_engine.dispose()
    return triage


def run_refresh(
    dsn: str,
    tenant_id: uuid.UUID,
    *,
    dry_run: bool,
    retire_states: tuple[str, ...],
    retire_only: bool,
    regenerate_only: bool,
    assess: bool,
    limit: int | None,
    output: Path | None,
) -> int:
    report: dict = {
        "tenant_id": str(tenant_id),
        "generated_at": datetime.now(UTC).isoformat(),
        "dry_run": dry_run,
    }

    async def _db_phase() -> tuple[list[dict], list[Pattern], dict]:
        engine = create_async_engine(dsn)
        async with AsyncSession(engine) as db:
            result = await _prepare_refresh(
                db,
                tenant_id,
                dry_run=dry_run,
                retire_states=retire_states,
                retire_only=retire_only,
                regenerate_only=regenerate_only,
            )
        await engine.dispose()
        return result

    _retired, patterns, partial = asyncio.run(_db_phase())
    report.update(partial)

    if not retire_only:
        if limit is not None:
            print(f"  (limit {limit})")
        regen_rows = regenerate_for_patterns(
            tenant_id,
            patterns,
            dry_run=dry_run,
            limit=limit,
        )
        report["regeneration"] = regen_rows
        ok = sum(1 for r in regen_rows if r.get("status") == "ok")
        skipped = sum(1 for r in regen_rows if r.get("status") == "skipped")
        errors = sum(1 for r in regen_rows if r.get("status") == "error")
        print(f"  ok={ok} skipped={skipped} errors={errors}")

    if assess and not dry_run and not retire_only:
        print("Assessing new corpus (candidate + approved)...")
        triage = asyncio.run(_run_assess(dsn, tenant_id))
        report["assessment"] = {
            "assessed": triage["assessed"],
            "total": triage["total_playbooks"],
            "pass_rate": triage["pass_rate"],
            "state_summary": triage["state_summary"],
            "errors": triage["errors"],
        }
        print(
            f"Assessed {triage['assessed']}/{triage['total_playbooks']}, "
            f"pass rate {triage['pass_rate']:.1%}"
        )

    if output:
        import json

        output.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"Wrote {output}")

    regen = report.get("regeneration") or []
    fatal = sum(1 for r in regen if r.get("status") == "error")
    return 0 if fatal == 0 or dry_run else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", default=settings.database_url)
    parser.add_argument("--tenant", type=uuid.UUID, required=True)
    parser.add_argument(
        "--retire-states",
        default=",".join(DEFAULT_RETIRE_STATES),
        help="Comma-separated lifecycle states to retire (default: candidate)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview counts only")
    parser.add_argument("--yes", action="store_true", help="Skip confirmation prompt")
    parser.add_argument(
        "--retire-only",
        action="store_true",
        help="Retire old playbooks without LLM regeneration",
    )
    parser.add_argument(
        "--regenerate-only",
        action="store_true",
        help="Regenerate missing playbooks without retiring",
    )
    parser.add_argument(
        "--no-assess",
        action="store_true",
        help="Skip batch assess after regeneration (default: assess when regenerating)",
    )
    parser.add_argument("--limit", type=int, default=None, help="Cap regeneration count")
    parser.add_argument("--output", type=Path, default=None, help="JSON report path")
    args = parser.parse_args()

    if args.retire_only and args.regenerate_only:
        print("Choose at most one of --retire-only and --regenerate-only", file=sys.stderr)
        return 1

    retire_states = tuple(s.strip() for s in args.retire_states.split(",") if s.strip())
    assess = not args.no_assess and not args.retire_only

    if not args.dry_run and not args.yes:
        action = []
        if not args.regenerate_only:
            action.append(f"retire {','.join(retire_states)} playbooks")
        if not args.retire_only:
            action.append("regenerate from patterns (LLM)")
        if assess:
            action.append("batch assess")
        print("Will:", ", ".join(action))
        print("Re-run with --yes to proceed, or --dry-run to preview.")
        return 1

    dsn = args.dsn
    if "+asyncpg" not in dsn:
        dsn = dsn.replace("postgresql://", "postgresql+asyncpg://", 1)

    return run_refresh(
        dsn,
        args.tenant,
        dry_run=args.dry_run,
        retire_states=retire_states,
        retire_only=args.retire_only,
        regenerate_only=args.regenerate_only,
        assess=assess,
        limit=args.limit,
        output=args.output,
    )


if __name__ == "__main__":
    raise SystemExit(main())
