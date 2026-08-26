"""Map PLAYBOOK_SPECIFICITY_AUDIT.md tables to database playbooks."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import psycopg2

AUDIT_PATH = r"C:\Users\omkar.patil\Downloads\PLAYBOOK_SPECIFICITY_AUDIT.md"


def parse_audit_tables():
    with open(AUDIT_PATH, "r", encoding="utf-8") as f:
        lines = f.readlines()

    zero_titles = []
    low_titles = []

    current_section = None
    for line in lines:
        if "## Flagged: 61 playbooks with ZERO concrete steps" in line:
            current_section = "zero"
            continue
        elif "## Flagged: 78 playbooks at 1–33% concrete" in line:
            current_section = "low"
            continue
        elif line.startswith("## ") and current_section:
            current_section = None

        if current_section in ("zero", "low") and line.strip().startswith("|"):
            parts = [p.strip() for p in line.strip().split("|")[1:-1]]
            if len(parts) >= 6 and parts[0] not in ("Concrete", "---:"):
                # Table format: | Concrete | Steps | Conf | Ungrounded | Risk | Playbook |
                playbook_title = parts[5]
                if current_section == "zero":
                    zero_titles.append(playbook_title)
                elif current_section == "low":
                    low_titles.append(playbook_title)

    print(f"Parsed from audit document:")
    print(f"  Zero concrete (0%): {len(zero_titles)} playbooks")
    print(f"  Low concrete (1-33%): {len(low_titles)} playbooks")
    return zero_titles, low_titles


def check_db():
    zero_titles, low_titles = parse_audit_tables()

    conn = psycopg2.connect("postgresql://postgres:root@localhost:5432/AEProdSupport")
    cur = conn.cursor()

    cur.execute("SELECT id, title, lifecycle_state FROM playbooks;")
    db_playbooks = {row[1].strip(): (row[0], row[2]) for row in cur.fetchall()}

    matched_zero = [t for t in zero_titles if t in db_playbooks]
    matched_low = [t for t in low_titles if t in db_playbooks]

    print(f"\nDatabase matching:")
    print(f"  Total DB playbooks: {len(db_playbooks)}")
    print(f"  Matched Zero-Concrete: {len(matched_zero)} of {len(zero_titles)}")
    print(f"  Matched Low-Concrete: {len(matched_low)} of {len(low_titles)}")

    unflagged_count = len(db_playbooks) - len(matched_zero) - len(matched_low)
    print(f"  Playbooks in Medium & High bands (>33% concrete): {unflagged_count}")

    conn.close()


if __name__ == "__main__":
    check_db()
