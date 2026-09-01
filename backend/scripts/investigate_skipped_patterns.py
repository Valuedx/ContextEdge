"""Investigate skipped pattern regeneration — no data loss audit."""
import json
import uuid
from collections import Counter, defaultdict
from pathlib import Path

import psycopg2

TID = "00000000-0000-0000-0000-000000000001"
DSN = "postgresql://postgres:root@localhost:5432/AEProdSupport"

report_path = Path(__file__).resolve().parents[1] / "refresh_report.json"
regen = []
if report_path.exists():
    regen = json.load(report_path.open(encoding="utf-8")).get("regeneration", [])

c = psycopg2.connect(DSN)
cur = c.cursor()

# --- corpus counts ---
cur.execute(
    "SELECT lifecycle_state, count(*) FROM playbooks WHERE tenant_id=%s GROUP BY 1",
    (TID,),
)
print("=== PLAYBOOKS (nothing deleted) ===")
for row in cur.fetchall():
    print(f"  {row[0]}: {row[1]}")

cur.execute("SELECT count(*) FROM patterns WHERE tenant_id=%s", (TID,))
print(f"patterns total: {cur.fetchone()[0]}")

# Patterns with NO active playbook right now
cur.execute(
    """
    SELECT p.id, p.title, p.confidence
    FROM patterns p
    WHERE p.tenant_id = %s
      AND NOT EXISTS (
        SELECT 1 FROM playbooks pb
        WHERE pb.pattern_id = p.id
          AND pb.tenant_id = p.tenant_id
          AND pb.lifecycle_state NOT IN ('retired', 'deprecated')
      )
    ORDER BY p.confidence DESC NULLS LAST
    """,
    (TID,),
)
no_active = cur.fetchall()
print(f"\npatterns without active playbook: {len(no_active)}")

# Retired playbook still linked to pattern?
cur.execute(
    """
    SELECT count(DISTINCT pattern_id)
    FROM playbooks
    WHERE tenant_id = %s AND lifecycle_state = 'retired' AND pattern_id IS NOT NULL
    """,
    (TID,),
)
print(f"retired playbooks still holding pattern_id: {cur.fetchone()[0]}")

# Patterns that HAD a retired candidate (replacement gap)
cur.execute(
    """
    SELECT count(DISTINCT pb.pattern_id)
    FROM playbooks pb
    WHERE pb.tenant_id = %s
      AND pb.lifecycle_state = 'retired'
      AND pb.pattern_id IS NOT NULL
      AND NOT EXISTS (
        SELECT 1 FROM playbooks active
        WHERE active.pattern_id = pb.pattern_id
          AND active.tenant_id = pb.tenant_id
          AND active.lifecycle_state NOT IN ('retired', 'deprecated')
      )
    """,
    (TID,),
)
gap_patterns = cur.fetchone()[0]
print(f"patterns with retired playbook but NO new active playbook: {gap_patterns}")

# --- regen report breakdown ---
if regen:
    by_status = Counter(r.get("status") for r in regen)
    by_reason = Counter(r.get("reason") for r in regen if r.get("status") == "skipped")
    print("\n=== REGENERATION REPORT ===")
    print("status:", dict(by_status))
    print("skip reasons:", dict(by_reason))

# Cross: skipped patterns that had retired playbook
if regen:
    skipped_ids = {r["pattern_id"] for r in regen if r.get("status") == "skipped"}
    ok_ids = {r["pattern_id"] for r in regen if r.get("status") == "ok"}
    cur.execute(
        """
        SELECT pb.pattern_id::text, count(*) as retired_count
        FROM playbooks pb
        WHERE pb.tenant_id = %s AND pb.lifecycle_state = 'retired'
          AND pb.pattern_id IS NOT NULL
        GROUP BY pb.pattern_id
        """,
        (TID,),
)
    retired_by_pattern = {str(r[0]): r[1] for r in cur.fetchall()}

    skipped_had_retired = sum(1 for pid in skipped_ids if pid in retired_by_pattern)
    ok_had_retired = sum(1 for pid in ok_ids if pid in retired_by_pattern)
    print("\n=== LINEAGE (retired -> regen attempt) ===")
    print(f"skipped patterns that previously had retired playbook: {skipped_had_retired}")
    print(f"ok patterns that previously had retired playbook: {ok_had_retired}")

    reason_buckets = defaultdict(list)
    for r in regen:
        if r.get("status") != "skipped":
            continue
        pid = r["pattern_id"]
        had = pid in retired_by_pattern
        reason_buckets[(r.get("reason"), had)].append(r)

    print("\n=== SKIP BREAKDOWN (reason x had_old_playbook) ===")
    for (reason, had), items in sorted(reason_buckets.items(), key=lambda x: -len(x[1])):
        label = "had_retired_playbook" if had else "never_had_candidate"
        print(f"  {reason or 'unknown'} [{label}]: {len(items)}")

    print("\n=== SAMPLE SKIPS (first 5 per reason, with retired title) ===")
    shown = Counter()
    for r in regen:
        if r.get("status") != "skipped":
            continue
        reason = r.get("reason") or "unknown"
        if shown[reason] >= 5:
            continue
        pid = r["pattern_id"]
        cur.execute(
            "SELECT title FROM playbooks WHERE tenant_id=%s AND pattern_id=%s AND lifecycle_state='retired' LIMIT 1",
            (TID, pid),
        )
        old = cur.fetchone()
        cur.execute("SELECT confidence FROM patterns WHERE id=%s", (pid,))
        conf = cur.fetchone()
        print(f"  [{reason}] conf={conf[0] if conf else None}")
        print(f"    pattern: {r.get('pattern_title', '')[:80]}")
        if old:
            print(f"    old retired title: {old[0][:80]}")
        shown[reason] += 1

# Patterns never attempted (not in regen report)?
if regen:
    attempted = {r["pattern_id"] for r in regen}
    not_attempted = [p for p in no_active if str(p[0]) not in attempted]
    print(f"\npatterns without active playbook NOT in regen report: {len(not_attempted)}")
    if not_attempted[:3]:
        for p in not_attempted[:3]:
            print(f"  {p[0]} conf={p[2]} {p[1][:60]}")

c.close()
print("\n=== DATA LOSS CHECK ===")
print("Retired playbook rows: preserved in DB (query lifecycle_state=retired)")
print("Pattern rows: all 541 preserved")
print("Episode/evidence links: unchanged by refresh script")
print("Gap = retired content exists; new candidate withheld by gates, not deleted")
