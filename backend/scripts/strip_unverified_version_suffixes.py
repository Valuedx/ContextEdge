"""Strip unverified 'Linked tickets specify' suffixes, then apply only
ticket-checked in-place replacements.

Investigation (2026-08-26) found 35 current steps had versions appended
without checking whether that version belonged on that step. Examples of
incorrect adds: AE 8.2.x on an ActiveMQ JAR upgrade; AE 8.2.3 on an S3
plugin 4.2 step; PostgreSQL 15 (the old version) next to 16.12/16.14.

This script:
1. Removes ` Linked tickets specify: ...` from current unpublished steps.
2. Updates a small set of steps where the linked ticket was re-read and
   the existing phrase can be replaced (not appended) with the ticket value.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import uuid

import psycopg2
from psycopg2.extras import Json, RealDictCursor, register_uuid

DSN = "postgresql://postgres:root@localhost:5432/AEProdSupport"
SUFFIX_RE = re.compile(r"\s*Linked tickets specify:\s*.+$", re.I)

# Playbook title → list of (old_substring, new_substring) applied AFTER suffix strip.
# Only replacements verified against the named ticket(s).
VERIFIED_REPLACEMENTS = {
    "ActiveMQ Client Connection Instability and Failover Recovery": [
        # Ticket 313308 names TCP 61614 only, not 61616.
        (
            "ActiveMQ transport port 61616 or 61614",
            "ActiveMQ transport port 61614",
        ),
    ],
    "Apache ActiveMQ Vulnerability Remediation": [
        # Ticket 428145: VAPT flagged log4j-core-2.25.3.jar in ActiveMQ.
        (
            "replace the flagged vulnerable dependency JAR files with secure versions in the library path",
            "replace log4j-core-2.25.3.jar in the ActiveMQ library path with a patched log4j-core JAR",
        ),
    ],
    "Security Vulnerability Remediation via Software Release": [
        # Tickets 272213 (AE 8.2.5 AppSec) and 277768 (PostgreSQL 11 → 15).
        (
            "the target release version required to patch the issue",
            "the patched version from the finding (AutomationEdge 8.2.5 for AppSec, or PostgreSQL 15 when the finding is PostgreSQL 11)",
        ),
        (
            "the target software release or upgraded component package",
            "AutomationEdge 8.2.5 (AppSec) or PostgreSQL 15 (PostgreSQL 11 findings)",
        ),
        (
            "Deliver the validated release package to the customer or production environment and apply the upgrade",
            "Deliver AutomationEdge 8.2.5 or PostgreSQL 15 (matching the finding) to the customer or production environment and apply the upgrade",
        ),
    ],
}


class UUIDJson(Json):
    def dumps(self, obj):
        return json.dumps(obj, default=str)


def step_text(step: dict) -> str:
    return (step.get("text") or step.get("instruction") or "").strip()


def strip_suffix(text: str) -> str:
    return SUFFIX_RE.sub("", text).rstrip(" .") + ("." if text.rstrip().endswith(".") else "")


def apply_verified(title: str, text: str) -> str:
    for old, new in VERIFIED_REPLACEMENTS.get(title, []):
        if old in text:
            text = text.replace(old, new)
    return text


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    register_uuid()
    conn = psycopg2.connect(DSN)
    conn.autocommit = False
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT set_config('app.bypass_rls', 'on', false)")
        cur.execute(
            """
            SELECT p.id, p.title, p.current_version_id, pv.steps, pv.published_at
            FROM playbooks p
            JOIN playbook_versions pv ON pv.id = p.current_version_id
            WHERE p.lifecycle_state <> 'retired'
            """
        )
        changed = []
        skipped_published = 0
        for row in cur.fetchall():
            if row["published_at"] is not None:
                steps = row["steps"] or []
                if any("Linked tickets specify" in step_text(s) for s in steps if isinstance(s, dict)):
                    skipped_published += 1
                continue
            steps = copy.deepcopy(row["steps"] or [])
            mutated = False
            new_steps = []
            for s in steps:
                if not isinstance(s, dict):
                    new_steps.append(s)
                    continue
                original = step_text(s)
                text = strip_suffix(original)
                text = apply_verified(row["title"], text)
                if text != original:
                    mutated = True
                    ns = copy.deepcopy(s)
                    ns["text"] = text
                    new_steps.append(ns)
                else:
                    new_steps.append(s)
            if mutated:
                changed.append(
                    {
                        "id": str(row["id"]),
                        "title": row["title"],
                        "version_id": str(row["current_version_id"]),
                        "before": [step_text(s) for s in steps if isinstance(s, dict)],
                        "after": [step_text(s) for s in new_steps if isinstance(s, dict)],
                    }
                )
                if args.apply:
                    cur.execute(
                        """
                        UPDATE playbook_versions
                        SET steps = %s
                        WHERE id = %s AND published_at IS NULL
                        """,
                        (UUIDJson(new_steps), row["current_version_id"]),
                    )

        print(f"Playbooks to correct: {len(changed)}")
        print(f"Published skipped: {skipped_published}")
        for item in changed:
            print("---", item["title"])
            for b, a in zip(item["before"], item["after"]):
                if b != a:
                    print("  -", b[-180:])
                    print("  +", a[-180:])

        if args.apply:
            conn.commit()
            print("Applied.")
        else:
            conn.rollback()
            print("Dry run. Re-run with --apply to write.")
        return 0
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
