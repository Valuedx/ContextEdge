"""Export skipped-pattern gap manifest (retired playbook lineage)."""
import json
from pathlib import Path

import psycopg2

TID = "00000000-0000-0000-0000-000000000001"
DSN = "postgresql://postgres:root@localhost:5432/AEProdSupport"
REPORT = Path(__file__).resolve().parents[1] / "refresh_report.json"
OUT = Path(__file__).resolve().parents[1] / "skipped_gap_manifest.json"

regen = json.load(REPORT.open(encoding="utf-8"))["regeneration"]
c = psycopg2.connect(DSN)
cur = c.cursor()

for tbl in ("patterns", "episodes", "pattern_evidence_links", "episode_evidence_links", "playbooks"):
    cur.execute(f"SELECT count(*) FROM {tbl} WHERE tenant_id=%s", (TID,))
    print(f"{tbl}: {cur.fetchone()[0]}")

cur.execute(
    "SELECT count(*) FROM playbooks WHERE tenant_id=%s AND lifecycle_state='retired' AND pattern_id IS NULL",
    (TID,),
)
print("retired without pattern_id:", cur.fetchone()[0])

low = [r for r in regen if r.get("reason") == "pattern_confidence_below_floor"]
cur.execute(
    "SELECT pattern_id::text FROM playbooks WHERE tenant_id=%s AND lifecycle_state='retired' AND pattern_id IS NOT NULL",
    (TID,),
)
retired_pids = {r[0] for r in cur.fetchall()}
print(f"low_conf skips with retired playbook: {sum(1 for r in low if r['pattern_id'] in retired_pids)}/{len(low)}")

gap = []
for r in regen:
    if r.get("status") != "skipped":
        continue
    pid = r["pattern_id"]
    cur.execute(
        """
        SELECT id::text, title FROM playbooks
        WHERE tenant_id=%s AND pattern_id=%s AND lifecycle_state='retired'
        ORDER BY updated_at DESC NULLS LAST LIMIT 1
        """,
        (TID, pid),
    )
    row = cur.fetchone()
    cur.execute("SELECT confidence FROM patterns WHERE id=%s", (pid,))
    conf = cur.fetchone()[0]
    gap.append(
        {
            "pattern_id": pid,
            "pattern_title": r.get("pattern_title"),
            "confidence": float(conf or 0),
            "skip_reason": r.get("reason"),
            "retired_playbook_id": row[0] if row else None,
            "retired_title": row[1] if row else None,
        }
    )

OUT.write_text(json.dumps({"tenant_id": TID, "count": len(gap), "patterns": gap}, indent=2), encoding="utf-8")
print("wrote", OUT, "rows", len(gap))
c.close()
