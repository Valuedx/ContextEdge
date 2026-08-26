"""Repair the text defects introduced by the 2026-08-26 playbook remediation.

Root cause (backend/scripts/remediate_playbook_corpus.py :: unhedge, lines 649-673):

  1. line 665  r"\\(?\\s*(?:such as|e\\.g\\.|for example)\\s+([^)\\n]+)\\)?"
     The leading `\\(?\\s*` consumes the space (and an optional open paren) before the
     hedge, and `repl` returns the replacement with no leading space.
       -> "an external API tool such as Postman"  becomes  "an external API toolPostman"

  2. line 665  the capture group `([^)\\n]+)` runs greedily to the next ')' or end of
     line, so the whole remainder of the sentence is inside the match; `repl` may then
     return only a coordinate extracted from the evidence, discarding that remainder.
       -> "...passing flags such as --disable-gpu and --disable-software-rasterizer or
           deploying an updated runner configuration JAR."
          becomes "...passing flags--disable-gpu."

  3. line 654  `.strip(".,;")` removes the sentence's terminal full stop.

  4. line 671  re.sub(r"\\s+([,.;])", r"\\1", new) deletes the space before ANY period,
     including the leading dot of a filename or directory. This fires on every step of
     an IMPROVE playbook, hedge or no hedge.
       -> "delete the psplugins and .process-studio directories"
          becomes "...and.process-studio directories"
       -> "(.xls, .xlsx, or .xlsb)"  becomes  "(.xls,.xlsx, or.xlsb)"

This script does NOT re-run the transform. It applies a pre-computed patch derived from
the pre-change backup dump (data/playbook_remediation_backup_2026-08-26/) joined against
docs/playbook_corpus_remediation/remediation_decisions.jsonl, so every replacement is
traceable to the original text rather than to a regex guess.

Fix classes
-----------
  restore-space      27 steps - defect 4 only. Re-inserts the deleted space before the
                     filename. Hedging is left exactly as the remediation left it.
  restore-original   51 steps - defects 1-3. The de-hedge damaged the sentence, and in
                     these cases it also added nothing: `repl` fell through to
                     `return example`, so only the words "such as" were deleted. Flagged
                     when any of: word fusion; >=2 content words lost; determiner
                     collision; or the example began with an article, which leaves two
                     bare noun phrases butted together ("the preferred technology a Python
                     script was blocked"). An example beginning with a bare identifier is
                     NOT flagged - "driver file RedshiftJDBC42-*.jar is present" reads
                     correctly and is left alone.
  terminal-period    15 steps - defect 3 only. Restores the full stop.
  reinsert-step       2 steps - steps deleted as "backup padding" that also carried the
                     ticket's actual fix action (javassist JAR swap; .pluginsconf delete).

Steps produced by strip_unverified_version_suffixes.VERIFIED_REPLACEMENTS are protected
and never touched - those five edits were checked against their tickets and are correct.

Safety
------
  * dry run by default; --apply commits.
  * only current versions with published_at IS NULL are written.
  * every replacement is guarded on an exact match of the expected current text. If the
    live row differs (someone edited it since), the step is skipped and reported - the
    script never overwrites text it did not predict.
  * idempotent: re-running after --apply finds nothing to do.

Usage
-----
    python backend/scripts/fix_remediation_defects.py                # dry run
    python backend/scripts/fix_remediation_defects.py --apply
    python backend/scripts/fix_remediation_defects.py --apply --restore-verification
"""

from __future__ import annotations

import argparse
import copy
import json
import re
from pathlib import Path

import psycopg2
from psycopg2.extras import Json, RealDictCursor, register_uuid

REPO = Path(__file__).resolve().parents[2]


def _dsn() -> str:
    """DATABASE_URL_SYNC from the repo .env, falling back to the value the other
    remediation scripts hardcode."""
    env = REPO / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("DATABASE_URL_SYNC="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return "postgresql://postgres:root@localhost:5432/AEProdSupport"


DSN = _dsn()
PATCH_PATH = REPO / "docs" / "playbook_corpus_remediation" / "remediation_defect_patch.json"
REPORT_PATH = REPO / "docs" / "playbook_corpus_remediation" / "defect_fix_result.json"

# Mirrors services/playbook_embedding.py :: MAX_EMBED_CHARS
MAX_EMBED_CHARS = 4_000
TRIGGER_BUDGET = 1_200


class UUIDJson(Json):
    def dumps(self, obj):
        return json.dumps(obj, default=str)


def step_text(step: dict) -> str:
    return (step.get("text") or step.get("instruction") or "").strip()


def step_label(step: dict) -> str:
    return (
        step.get("title")
        or step.get("text")
        or step.get("action")
        or step.get("instruction")
        or ""
    ).strip()


def flatten_triggers(trigger_conditions) -> str:
    """Same shape as services/playbook_embedding.build_playbook_embedding_text."""
    out: list[str] = []
    budget = TRIGGER_BUDGET

    def walk(node):
        nonlocal budget
        if budget <= 0:
            return
        if isinstance(node, str):
            s = node.strip()
            if s:
                out.append(s)
                budget -= len(s)
        elif isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(trigger_conditions)
    return " ".join(out)[:TRIGGER_BUDGET]


def build_lexical_text(title, description, trigger_conditions, steps) -> str:
    """Reproduce the application's composer so the stored text stops diverging from it.

    remediate_playbook_corpus.py wrote `title + description + ALL step texts` capped at
    8000 chars with no trigger conditions; the app writes `title + description +
    trigger_conditions + the first 20 step LABELS` capped at 4000. The next PATCH or
    approval would have silently rewritten every remediated row to this shape anyway.
    """
    parts = [(title or "").strip()]
    if description:
        parts.append(description.strip())
    trig = flatten_triggers(trigger_conditions)
    if trig:
        parts.append(trig)
    for step in (steps or [])[:20]:
        if isinstance(step, dict):
            label = step_label(step)
            if label:
                parts.append(label)
    return " ".join(p for p in parts if p)[:MAX_EMBED_CHARS]


# --------------------------------------------------------------------------- #
# G6 - the five ticket-checked value corrections. Printed for review, never edited.
# --------------------------------------------------------------------------- #
REVIEW_NOTES = [
    (
        "ActiveMQ Client Connection Instability and Failover Recovery",
        "ticket 313308 - 'slave failed to bind TCP port 61614 during takeover'. "
        "Narrowing '61616 or 61614' to 61614 matches the ticket. No action.",
    ),
    (
        "Apache ActiveMQ Vulnerability Remediation",
        "ticket 428145 - VAPT flagged log4j-core-2.25.3.jar as the VULNERABLE artefact. "
        "The step reads 'replace log4j-core-2.25.3.jar ... with a patched log4j-core JAR', "
        "which is the correct direction. Worth a human sanity check that 2.25.3 is really "
        "what the scan reported, since that is a recent log4j-core release.",
    ),
    (
        "Security Vulnerability Remediation via Software Release",
        "tickets 272213 / 277768 - AE 8.2.5 and PostgreSQL 15 are named conditionally "
        "('for AppSec' / 'when the finding is PostgreSQL 11'), which is faithful. Note both "
        "tickets closed with the upgrade PENDING or UAT-only, so the playbook describes an "
        "intended target, not a verified production fix.",
    ),
    (
        "Agent Upgrade Feature Misunderstanding and Clarification",
        "The remediation summary claims an 'Agent upgrade bug branch -> plugin release 4.5' "
        "correction from ticket 219894. No such entry exists in "
        "strip_unverified_version_suffixes.VERIFIED_REPLACEMENTS, so it was either never "
        "applied or applied out of band. 219894 is a ServiceNow Plugin 4.4 defect with a "
        "fix promised in an upcoming 4.5 - verify before relying on it.",
    ),
]


def load_patch() -> dict:
    if not PATCH_PATH.exists():
        raise SystemExit(f"patch file not found: {PATCH_PATH}")
    return json.loads(PATCH_PATH.read_text(encoding="utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="commit; otherwise dry run")
    ap.add_argument("--no-space", action="store_true", help="skip whitespace repairs")
    ap.add_argument("--no-restore", action="store_true", help="skip original-text restores")
    ap.add_argument("--no-period", action="store_true", help="skip terminal-period repairs")
    ap.add_argument("--no-reinsert", action="store_true", help="skip step re-insertion")
    ap.add_argument(
        "--restore-verification",
        action="store_true",
        help="also re-insert verification steps removed as padding (opt-in)",
    )
    ap.add_argument(
        "--no-lexical",
        action="store_true",
        help="do not refresh lexical_search_text on touched playbooks",
    )
    args = ap.parse_args()

    patch = load_patch()
    edits: list[dict] = []
    if not args.no_space:
        edits += patch.get("space", [])
    if not args.no_restore:
        edits += patch.get("restore", [])
    if not args.no_period:
        edits += patch.get("period", [])
    inserts = [] if args.no_reinsert else list(patch.get("reinsert", []))
    if args.restore_verification:
        inserts += patch.get("reinsert_verification", [])

    by_pb: dict[str, dict] = {}
    for e in edits:
        by_pb.setdefault(e["playbook_id"], {"edits": [], "inserts": []})["edits"].append(e)
    for e in inserts:
        by_pb.setdefault(e["playbook_id"], {"edits": [], "inserts": []})["inserts"].append(e)

    register_uuid()
    conn = psycopg2.connect(DSN)
    conn.autocommit = False
    applied, skipped, missing = [], [], []
    committed = False
    touched_playbooks = 0

    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT set_config('app.bypass_rls', 'on', false)")

        for pb_id, work in by_pb.items():
            cur.execute(
                """
                SELECT p.id, p.title, p.description, p.lifecycle_state,
                       p.current_version_id, pv.steps, pv.trigger_conditions, pv.published_at
                FROM playbooks p
                JOIN playbook_versions pv ON pv.id = p.current_version_id
                WHERE p.id = %s
                """,
                (pb_id,),
            )
            row = cur.fetchone()
            if row is None:
                missing.append({"playbook_id": pb_id, "why": "playbook or current version not found"})
                continue
            if row["published_at"] is not None:
                skipped.append({"playbook_id": pb_id, "title": row["title"],
                                "why": "current version is published - not editable"})
                continue

            steps = copy.deepcopy(row["steps"] or [])
            mutated = False
            local: list[dict] = []

            # ---- in-place text edits, guarded on an exact match of the current text ----
            # Matched by text, not by index: re-inserting a step shifts every later
            # index, so a positional guard reports spurious misses on a second run.
            for e in sorted(work["edits"], key=lambda x: x["order"]):
                if any(isinstance(s, dict) and step_text(s) == e["new_text"] for s in steps):
                    continue  # already fixed - idempotent
                hit = next(
                    (
                        n
                        for n, s in enumerate(steps)
                        if isinstance(s, dict) and step_text(s) == e["current_text"]
                    ),
                    None,
                )
                if hit is None:
                    at = e["order"]
                    live = step_text(steps[at]) if at < len(steps) and isinstance(steps[at], dict) else ""
                    skipped.append({**_ident(e), "why": "live text differs from expected",
                                    "live": live[:200], "expected": e["current_text"][:200]})
                    continue
                steps[hit] = copy.deepcopy(steps[hit])
                steps[hit]["text"] = e["new_text"]
                mutated = True
                local.append({**_ident(e), "before": e["current_text"], "after": e["new_text"]})

            # ---- step re-insertion, guarded on absence ----
            for e in sorted(work["inserts"], key=lambda x: x["after_order"]):
                if any(isinstance(s, dict) and step_text(s) == e["text"] for s in steps):
                    continue  # already present - idempotent
                new_step = copy.deepcopy(e["step"])
                pos = min(max(e["after_order"] + 1, 0), len(steps))
                steps.insert(pos, new_step)
                mutated = True
                local.append({**_ident(e), "before": None, "after": e["text"]})

            if not mutated:
                continue

            for n, s in enumerate(steps, start=1):
                if isinstance(s, dict) and "order" in s:
                    s["order"] = n

            touched_playbooks += 1
            applied.extend(local)

            if args.apply:
                cur.execute(
                    "UPDATE playbook_versions SET steps = %s WHERE id = %s AND published_at IS NULL",
                    (UUIDJson(steps), row["current_version_id"]),
                )
                if not args.no_lexical:
                    cur.execute(
                        "UPDATE playbooks SET lexical_search_text = %s, updated_at = now() WHERE id = %s",
                        (
                            build_lexical_text(row["title"], row["description"],
                                               row["trigger_conditions"], steps),
                            row["id"],
                        ),
                    )

        # Commit (or roll back) BEFORE printing anything long. Printing first meant a
        # closed stdout - `| head`, `| more`, a killed pager - raised BrokenPipeError
        # mid-report, unwound into the except clause, and rolled the whole apply back
        # while still looking like it had succeeded.
        if args.apply:
            conn.commit()
            committed = True
        else:
            conn.rollback()
            committed = False

        # ------------------------------------------------------------------ #
        print(f"playbooks touched      : {touched_playbooks}")
        print(f"step edits applied     : {sum(1 for a in applied if a['before'] is not None)}")
        print(f"steps re-inserted      : {sum(1 for a in applied if a['before'] is None)}")
        print(f"skipped (guard failed) : {len(skipped)}")
        print(f"not found              : {len(missing)}")
        print()
        for a in applied:
            print(f"--- {a['title']}  step {a['order'] + 1 if a.get('order') is not None else '+'}  [{a['fix']}]")
            if a["before"] is not None:
                print(f"  - {a['before'][:200]}")
            print(f"  + {a['after'][:200]}")
        if skipped:
            print("\nSKIPPED - review by hand:")
            for s in skipped:
                print(f"  {s.get('title', s.get('playbook_id'))}: {s['why']}")

        print("\n" + "=" * 72)
        print("NOT CHANGED BY THIS SCRIPT - human review required (G6)")
        print("=" * 72)
        for title, note in REVIEW_NOTES:
            print(f"\n* {title}\n  {note}")

        print("\n" + "=" * 72)
        print("STILL OPEN after this script")
        print("=" * 72)
        print(
            "  G1  Every retrieval arm requires lifecycle_state='approved' AND a published\n"
            "      version. The corpus is 100% 'candidate' with 0 published versions, so\n"
            "      /api/v1/runtime/match returns nothing. These repairs have no runtime\n"
            "      effect until the corpus is approved and its current versions published.\n"
            "  G11 playbooks.embedding was never recomputed. Once the corpus is published,\n"
            "      run workers/playbook_tasks backfill with refresh_stale=True; until then\n"
            "      the vector arm would match pre-remediation text.\n"
            "  G7  48 of the 53 playbooks both audits call empty are still 'candidate'.\n"
            "  G8  109 KEEP playbooks are rated CRITICAL GAP by the corpus audit - unadjudicated.\n"
            "  G9  33 escalation steps remain deleted (--restore-verification covers only\n"
            "      the verification steps)."
        )

        REPORT_PATH.write_text(
            json.dumps(
                {
                    "mode": "apply" if args.apply else "dry-run",
                    "playbooks_touched": touched_playbooks,
                    "applied": applied,
                    "skipped": skipped,
                    "missing": missing,
                },
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )
        print(f"\nreport written: {REPORT_PATH}")
        print("Applied." if committed else "Dry run. Re-run with --apply to write.")
        return 0
    except BrokenPipeError:
        # stdout went away mid-report; the transaction is already resolved above.
        return 0
    except Exception:
        if not committed:
            conn.rollback()
        raise
    finally:
        conn.close()


def _ident(e: dict) -> dict:
    return {
        "playbook_id": e["playbook_id"],
        "title": e["title"],
        "order": e.get("order", e.get("after_order")),
        "fix": e["fix"],
        "reasons": e.get("reasons", []),
    }


if __name__ == "__main__":
    raise SystemExit(main())
