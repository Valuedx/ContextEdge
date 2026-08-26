"""Audit and score playbook concreteness.

CAVEAT - read before quoting any number this produces
-----------------------------------------------------
This script does NOT reproduce the figures published in PLAYBOOK_SPECIFICITY_AUDIT.md
(1,198 concrete steps / 59.7%; bands 61 / 78 / 132 / 169). With the bug below fixed it
scores the same pre-change corpus at 956 steps / 47.7%; bands 130 / 75 / 100 / 135.
Four different concreteness definitions exist across this repo and its audit documents,
and no committed code regenerates the specificity audit's numbers. Do not difference a
band count from one against a band count from another.

The only reproducible before/after series is `remediate_playbook_corpus.remaining_quality`
(concrete = `extract_coords(t) or AE_PRODUCT.search(t)`), which is what
`verify_playbook_corpus.py` reports and what `apply_summary.json` was written with. On
that scorer the corpus went 127 -> 112 playbooks in the 0% band across the remediation
and the subsequent defect repair.

Fixed 2026-08-26 - the case-insensitivity bug
---------------------------------------------
`COMBINED_RE` was compiled with `re.IGNORECASE` over all twelve patterns. Pattern 10 is
the CamelCase detector:

    r"\\b[A-Z][a-z0-9]+[A-Z][a-zA-Z0-9]*\\b"

Under IGNORECASE that reduces to "a word of three or more letters", so it matched every
ordinary English word and the script reported **100% of steps concrete, every playbook in
the 67-100% band** - a meaningless all-clear. The structural patterns that depend on case
are now compiled case-sensitively; the word and extension lists stay case-insensitive.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import psycopg2

REPO = Path(__file__).resolve().parents[2]

# Case-insensitive: filenames, artifact words, paths, ports, versions, quoted literals,
# placeholders, CLI flags, dotted identifiers, technical nouns.
CASE_INSENSITIVE_PATTERNS = [
    r"\b[\w-]+\.(jar|log|properties|xml|json|conf|yml|yaml|ini|bat|sh|ps1|sql|csv|xlsx|dll|exe)\b",
    r"\b(JAR|keystore|truststore|classpath|stdout|stderr|jvm|JVM)\b",
    r"\b[a-z]{2,8}\.[a-z0-9_]+\.[A-Za-z0-9_.]+\b",
    r"([A-Za-z]:\\[^ \n\t]+|<[^>]+>\\[^ \n\t]+|/[a-zA-Z0-9_.-]+/[a-zA-Z0-9_./-]+)",
    r"\b(port\s*\d+|\:\d{2,5}\b)",
    r"\bv?\d+\.\d+(\.\d+)?\b",
    r'\"[^\"]+\"|`[^`]+`|\'[^\']+\'',
    r"<[a-zA-Z0-9_-]+>",
    r"\s-[a-zA-Z]|\s--[a-zA-Z0-9_-]+|\btscon\b|\bnetstat\b|\bping\b|\bcurl\b|\bsystemctl\b",
    r"\b[a-z0-9_]+(\.[a-z0-9_]+)+\b",
    r"\b(heap|cipher|certificate|cert|Xmx|Xms|OOM|OutOfMemory|JDBC|RDP|SSO|SAML|LDAP|TLS|SSL|OAuth|GUID|UUID|regex)\b",
]

# Case-SENSITIVE: CamelCase identifiers. Compiling this with IGNORECASE is the bug above.
CASE_SENSITIVE_PATTERNS = [
    r"\b[A-Z][a-z0-9]+[A-Z][a-zA-Z0-9]*\b",
]

CI_RE = re.compile("|".join(CASE_INSENSITIVE_PATTERNS), re.IGNORECASE)
CS_RE = re.compile("|".join(CASE_SENSITIVE_PATTERNS))


def _dsn() -> str:
    env = REPO / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("DATABASE_URL_SYNC="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return "postgresql://postgres:root@localhost:5432/AEProdSupport"


def is_step_concrete(step_text: str) -> bool:
    if not step_text:
        return False
    return bool(CI_RE.search(step_text)) or bool(CS_RE.search(step_text))


def analyze_playbooks(include_retired: bool = False):
    conn = psycopg2.connect(_dsn())
    cur = conn.cursor()
    cur.execute(
        """
        SELECT p.id, p.title, pv.id, pv.steps, pv.playbook_confidence, p.lifecycle_state
        FROM playbooks p
        JOIN playbook_versions pv ON pv.id = p.current_version_id
        {}
        ORDER BY p.title;
        """.format("" if include_retired else "WHERE p.lifecycle_state <> 'retired'")
    )
    rows = cur.fetchall()
    conn.close()

    buckets = {"zero": [], "low": [], "med": [], "high": []}
    total_steps = concrete_steps = 0

    for pid, title, vid, steps, conf, state in rows:
        steps_list = steps if isinstance(steps, list) else []
        n = len(steps_list)
        k = sum(1 for s in steps_list if is_step_concrete(s.get("text", "")))
        total_steps += n
        concrete_steps += k
        pct = round(k / n, 4) if n else 0.0
        entry = {
            "id": str(pid),
            "version_id": str(vid),
            "title": title,
            "lifecycle_state": state,
            "total_steps": n,
            "concrete_steps": k,
            "concreteness": pct,
            "confidence": float(conf) if conf is not None else None,
        }
        if pct == 0.0:
            buckets["zero"].append(entry)
        elif pct <= 0.334:
            buckets["low"].append(entry)
        elif pct <= 0.667:
            buckets["med"].append(entry)
        else:
            buckets["high"].append(entry)

    print(f"Playbooks analyzed: {len(rows)}   steps: {total_steps}")
    print(f"Concrete steps: {concrete_steps} ({100.0 * concrete_steps / total_steps:.1f}%)"
          if total_steps else "Concrete steps: 0")
    print(f"Band 0%      (no actionable artifact) : {len(buckets['zero'])}")
    print(f"Band 1-33%   (low)                    : {len(buckets['low'])}")
    print(f"Band 34-66%  (medium)                 : {len(buckets['med'])}")
    print(f"Band 67-100% (actionable)             : {len(buckets['high'])}")
    print(
        "\nNOTE: these bands are NOT comparable to PLAYBOOK_SPECIFICITY_AUDIT.md or to\n"
        "apply_summary.json - all three use different definitions of 'concrete'.\n"
        "For a like-for-like before/after series use verify_playbook_corpus.py."
    )
    return buckets


if __name__ == "__main__":
    result = analyze_playbooks()
    out = REPO / "docs" / "playbook_corpus_remediation" / "concreteness_scores.json"
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"\nwritten: {out}")
