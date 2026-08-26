import json
import psycopg2
from scripts.map_audit_to_db import parse_audit_tables

def main():
    zero_titles, _ = parse_audit_tables()

    conn = psycopg2.connect("postgresql://postgres:root@localhost:5432/AEProdSupport")
    cur = conn.cursor()
    cur.execute("""
        SELECT p.id, p.title, p.risk_tier, pv.playbook_confidence, pv.steps
        FROM playbooks p
        JOIN playbook_versions pv ON pv.id = p.current_version_id
        WHERE p.title = ANY(%s)
        ORDER BY p.risk_tier DESC, pv.playbook_confidence DESC, p.title ASC;
    """, (zero_titles,))
    rows = cur.fetchall()
    conn.close()

    print(f"Total fetched: {len(rows)}")
    for i in range(min(5, len(rows))):
        pid, title, risk, conf, steps = rows[i]
        print(f"\n=== {title} ({risk}, conf: {conf}) ===")
        for idx, s in enumerate(steps, 1):
            print(f"  Step {idx}: {s.get('text')}")

if __name__ == "__main__":
    main()
