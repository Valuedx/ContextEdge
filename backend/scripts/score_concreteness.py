"""Audit and score playbook concreteness according to PLAYBOOK_SPECIFICITY_AUDIT.md."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import psycopg2

PATTERNS = [
    r"\b[\w-]+\.(jar|log|properties|xml|json|conf|yml|yaml|ini|bat|sh|ps1|sql|csv|xlsx|dll|exe)\b",
    r"\b(JAR|keystore|truststore|classpath|stdout|stderr|jvm|JVM)\b",
    r"\b[a-z]{2,8}\.[a-z0-9_]+\.[A-Za-z0-9_.]+\b",
    r"([A-Za-z]:\\[^ \n\t]+|<[^>]+>\\[^ \n\t]+|/[a-zA-Z0-9_.-]+/[a-zA-Z0-9_./-]+)",
    r"\b(port\s*\d+|\:\d{2,5}\b)",
    r"\bv?\d+\.\d+(\.\d+)?\b",
    r'\"[^\"]+\"|`[^`]+`|\'[^\']+\'',
    r"<[a-zA-Z0-9_-]+>",
    r"\s-[a-zA-Z]|\s--[a-zA-Z0-9_-]+|\btscon\b|\bnetstat\b|\bping\b|\bcurl\b|\bsystemctl\b",
    r"\b[A-Z][a-z0-9]+[A-Z][a-zA-Z0-9]*\b",
    r"\b[a-z0-9_]+(\.[a-z0-9_]+)+\b",
    r"\b(heap|cipher|certificate|cert|Xmx|Xms|OOM|OutOfMemory|JDBC|RDP|SSO|SAML|LDAP|TLS|SSL|OAuth|GUID|UUID|regex)\b",
]

COMBINED_RE = re.compile("|".join(PATTERNS), re.IGNORECASE)


def is_step_concrete(step_text: str) -> bool:
    if not step_text:
        return False
    return bool(COMBINED_RE.search(step_text))


def analyze_playbooks():
    conn = psycopg2.connect("postgresql://postgres:root@localhost:5432/AEProdSupport")
    cur = conn.cursor()

    cur.execute("""
        SELECT p.id, p.title, pv.id, pv.steps, pv.playbook_confidence
        FROM playbooks p
        JOIN playbook_versions pv ON pv.id = p.current_version_id
        ORDER BY p.title;
    """)
    rows = cur.fetchall()

    zero_concrete = []
    low_concrete = []
    med_concrete = []
    high_concrete = []

    for pid, title, vid, steps, conf in rows:
        steps_list = steps if isinstance(steps, list) else []
        total_steps = len(steps_list)
        if total_steps == 0:
            pct = 0.0
            concrete_count = 0
        else:
            concrete_count = sum(1 for s in steps_list if is_step_concrete(s.get("text", "")))
            pct = round(concrete_count / total_steps, 4)

        entry = {
            "id": str(pid),
            "version_id": str(vid),
            "title": title,
            "total_steps": total_steps,
            "concrete_steps": concrete_count,
            "concreteness": pct,
            "confidence": conf,
        }

        if pct == 0.0:
            zero_concrete.append(entry)
        elif pct <= 0.334:
            low_concrete.append(entry)
        elif pct <= 0.667:
            med_concrete.append(entry)
        else:
            high_concrete.append(entry)

    conn.close()

    print(f"Total playbooks analyzed: {len(rows)}")
    print(f"Band 0% Concrete (Fluff): {len(zero_concrete)}")
    print(f"Band 1-33% Concrete (Low): {len(low_concrete)}")
    print(f"Band 34-66% Concrete (Medium): {len(med_concrete)}")
    print(f"Band 67-100% Concrete (High/Actionable): {len(high_concrete)}")

    return {
        "zero": zero_concrete,
        "low": low_concrete,
        "med": med_concrete,
        "high": high_concrete,
    }


if __name__ == "__main__":
    analyze_playbooks()
