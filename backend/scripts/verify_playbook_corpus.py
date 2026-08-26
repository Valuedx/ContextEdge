"""Verify the state of the AEProdSupport playbook corpus after the defect repair.

Re-runs, against the live database, every check from
docs/playbook_corpus_remediation/REMEDIATION_GAP_VALIDATION.md, and writes a machine
readable verdict to docs/playbook_corpus_remediation/corpus_verification.json.

Read-only. It opens no transaction that writes and never calls commit.

    python backend/scripts/verify_playbook_corpus.py

Checks
------
  A  corpus shape           playbook / version / step counts, lifecycle spread
  B  referential integrity  broken current_version pointers, empty active playbooks,
                            orphan versions
  C  residual text defects  every current step diffed against its pre-remediation
                            original in playbook_original_steps.json, scanning for the
                            four unhedge() defects. This is the check that says whether
                            the repair worked.
  D  concreteness           scored with remediate_playbook_corpus.remaining_quality's
                            own scorer, so the bands are directly comparable to
                            apply_summary.json - and with the pre-change baseline
                            printed alongside, which apply_summary.json never was.
  E  retrievability         whether anything is actually reachable by an agent
  F  step-shape regressions playbooks left at <=2 steps, escalation/verification gaps
"""

from __future__ import annotations

import difflib
import json
import re
import sys
from collections import Counter
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = Path(__file__).resolve().parent
DOCS = REPO / "docs" / "playbook_corpus_remediation"
REF_PATH = DOCS / "playbook_original_steps.json"
OUT_PATH = DOCS / "corpus_verification.json"

sys.path.insert(0, str(SCRIPTS))
from remediate_playbook_corpus import (  # noqa: E402
    AE_PRODUCT,
    BARE_INSPECT_RE,
    FILLER_RE,
    extract_coords,
    step_text,
)
from strip_unverified_version_suffixes import VERIFIED_REPLACEMENTS  # noqa: E402

# Text produced by a ticket-checked replacement is a deliberate divergence from the
# original, not a defect. Recognised and excluded from check C.
VERIFIED_TEXT = {new for pairs in VERIFIED_REPLACEMENTS.values() for _, new in pairs}

# Pre-change baseline, computed from the backup dump with the scorer imported above.
BASELINE = {
    "playbooks": 440,
    "steps": 2006,
    "concrete_steps": 983,
    "bands": {"0%": 127, "1-33%": 41, "34-66%": 99, "67-100%": 173},
    "generic_or_bare_inspect_steps": 69,
    "hedged_steps": 231,
}

# What apply_summary.json reported immediately after the remediation, same scorer.
POST_REMEDIATION = {
    "playbooks": 420,
    "steps": 1847,
    "bands": {"0%": 109, "1-33%": 32, "34-66%": 100, "67-100%": 179},
    "generic_or_bare_inspect_steps": 13,
}

EXTS = (
    r"jar|war|xls[xbm]?|psw|psrc|pac|eml|msg|zip|png|jpe?g|svg|gif|properties|"
    r"process-studio|pluginconf|pluginsconf|settings|prop|py|bat|sh|log|xml|json|"
    r"csv|conf|pem|jks|crt|lic|dll|exe|ini|yml|yaml"
)
GLUE_EXT = re.compile(r"(?<=[A-Za-z0-9,])(\.(?:%s))\b" % EXTS)
FUSE = re.compile(r"\b[a-z]{3,}[A-Z][a-z]{2,}\b")
ARTICLE_EXAMPLE = re.compile(r"(?:such as|e\.g\.,?|for example,?)\s+(the|a|an|its|their)\b", re.I)
HEDGE = re.compile(r"\b(such as|e\.g\.|for example)\b", re.I)
WORD = re.compile(r"[A-Za-z0-9][\w./\\:<>=+*-]*")
ESCALATE = re.compile(r"\b(escalat|raise (a )?(ticket|case))", re.I)
VERIFY = re.compile(r"^\s*(verify|validate|confirm|test|re-?run)", re.I)


def _dsn() -> str:
    env = REPO / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("DATABASE_URL_SYNC="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return "postgresql://postgres:root@localhost:5432/AEProdSupport"


def is_concrete(text: str) -> bool:
    return bool(extract_coords(text) or AE_PRODUCT.search(text))


def band_of(pct: float) -> str:
    if pct == 0:
        return "0%"
    if pct <= 33:
        return "1-33%"
    if pct <= 66:
        return "34-66%"
    return "67-100%"


def delta(now: int, was: int) -> str:
    d = now - was
    return f"{now} ({d:+d})" if d else f"{now} (=)"


def main() -> int:
    if not REF_PATH.exists():
        raise SystemExit(
            f"reference file not found: {REF_PATH}\n"
            "It carries the pre-remediation step texts and is what check C diffs against."
        )
    ref = json.loads(REF_PATH.read_text(encoding="utf-8"))["playbooks"]

    conn = psycopg2.connect(_dsn())
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT set_config('app.bypass_rls', 'on', false)")

    out: dict = {}

    # ---------------------------------------------------------------- A: shape
    cur.execute("SELECT lifecycle_state, count(*) n FROM playbooks GROUP BY 1 ORDER BY 1")
    lifecycle = {r["lifecycle_state"]: r["n"] for r in cur.fetchall()}
    cur.execute("SELECT count(*) n FROM playbook_versions")
    n_versions = cur.fetchone()["n"]
    cur.execute("SELECT count(*) n FROM playbook_versions WHERE published_at IS NOT NULL")
    n_published = cur.fetchone()["n"]

    cur.execute(
        """
        SELECT p.id::text AS id, p.title, p.risk_tier, p.lifecycle_state,
               pv.steps, pv.published_at
        FROM playbooks p
        JOIN playbook_versions pv ON pv.id = p.current_version_id
        WHERE p.lifecycle_state <> 'retired'
        ORDER BY p.title
        """
    )
    active = cur.fetchall()

    total_steps = sum(len(r["steps"] or []) for r in active)
    out["A_shape"] = {
        "playbooks_total": sum(lifecycle.values()),
        "lifecycle": lifecycle,
        "versions": n_versions,
        "published_versions": n_published,
        "active_playbooks": len(active),
        "active_steps": total_steps,
    }

    # ------------------------------------------------- B: referential integrity
    cur.execute(
        """
        SELECT count(*) n FROM playbooks p
        LEFT JOIN playbook_versions pv ON pv.id = p.current_version_id
        WHERE p.current_version_id IS NOT NULL AND pv.id IS NULL
        """
    )
    broken_ptr = cur.fetchone()["n"]
    cur.execute(
        """
        SELECT count(*) n FROM playbook_versions pv
        LEFT JOIN playbooks p ON p.id = pv.playbook_id
        WHERE p.id IS NULL
        """
    )
    orphan_versions = cur.fetchone()["n"]
    empty_active = [r["title"] for r in active if not (r["steps"] or [])]
    out["B_integrity"] = {
        "broken_current_version_pointers": broken_ptr,
        "orphan_versions": orphan_versions,
        "active_playbooks_with_zero_steps": len(empty_active),
        "empty_titles": empty_active,
    }

    # --------------------------------------------------- C: residual text defects
    defects: list[dict] = []
    unmatched = 0
    verified_edits = 0
    for r in active:
        original = (ref.get(r["id"]) or {}).get("steps")
        if original is None:
            unmatched += 1
            continue
        for s in r["steps"] or []:
            t = step_text(s)
            if t in original:
                continue
            if any(v in t for v in VERIFIED_TEXT):
                verified_edits += 1
                continue
            close = difflib.get_close_matches(t, original, n=1, cutoff=0.30)
            if not close:
                continue
            o = close[0]
            reasons = []
            if any(
                m.group(1) not in o and (" " + m.group(1)) in o
                for m in GLUE_EXT.finditer(t)
            ):
                reasons.append("glue-ext")
            if any(m.group(0) not in o for m in FUSE.finditer(t)):
                reasons.append("word-fusion")
            if ARTICLE_EXAMPLE.search(o) and not ARTICLE_EXAMPLE.search(t):
                reasons.append("article-example-collapsed")
            ow = Counter(w.lower() for w in WORD.findall(HEDGE.sub(" ", o)))
            nw = Counter(w.lower() for w in WORD.findall(t))
            lost = sum((ow - nw).values())
            if lost >= 2:
                reasons.append(f"content-loss:{lost}w")
            if reasons:
                defects.append(
                    {"title": r["title"], "reasons": reasons, "original": o, "current": t}
                )
    out["C_text_defects"] = {
        "residual_defects": len(defects),
        "ticket_verified_edits_excluded": verified_edits,
        "playbooks_not_in_reference": unmatched,
        "detail": defects,
    }

    # ------------------------------------------------------------ D: concreteness
    bands = Counter()
    concrete_steps = generic = hedged = 0
    for r in active:
        steps = r["steps"] or []
        k = 0
        for s in steps:
            t = step_text(s)
            if is_concrete(t):
                k += 1
            elif FILLER_RE.search(t) or BARE_INSPECT_RE.match(t):
                generic += 1
            if HEDGE.search(t):
                hedged += 1
        concrete_steps += k
        bands[band_of(100.0 * k / len(steps) if steps else 0.0)] += 1
    out["D_concreteness"] = {
        "scorer": "remediate_playbook_corpus.remaining_quality (extract_coords or AE_PRODUCT)",
        "bands": dict(bands),
        "concrete_steps": concrete_steps,
        "concrete_pct": round(100.0 * concrete_steps / total_steps, 1) if total_steps else 0.0,
        "generic_or_bare_inspect_steps": generic,
        "hedged_steps": hedged,
        "baseline_pre_change": BASELINE,
        "post_remediation_reported": POST_REMEDIATION,
    }

    # ------------------------------------------------------------ E: retrievable
    cur.execute(
        """
        SELECT count(*) n
        FROM playbooks p
        JOIN playbook_versions pv ON pv.playbook_id = p.id
        WHERE p.lifecycle_state = 'approved' AND pv.published_at IS NOT NULL
        """
    )
    retrievable = cur.fetchone()["n"]
    out["E_retrievability"] = {
        "agent_retrievable_playbooks": retrievable,
        "note": (
            "search/playbook_candidates.py:55 gates every arm on lifecycle_state='approved'; "
            "hybrid_ranker.py:384-391 additionally drops playbooks with no published version. "
            "Zero here means /api/v1/runtime/match returns nothing for this tenant."
        ),
    }

    # -------------------------------------------------------- F: step-shape checks
    short = [
        {"title": r["title"], "risk": r["risk_tier"], "steps": len(r["steps"] or [])}
        for r in active
        if len(r["steps"] or []) <= 2
    ]
    with_escalation = sum(
        1 for r in active if any(ESCALATE.search(step_text(s)) for s in (r["steps"] or []))
    )
    with_verification = sum(
        1 for r in active if any(VERIFY.match(step_text(s)) for s in (r["steps"] or []))
    )
    out["F_step_shape"] = {
        "playbooks_with_two_or_fewer_steps": len(short),
        "detail": short,
        "playbooks_with_an_escalation_step": with_escalation,
        "playbooks_with_a_verification_step": with_verification,
    }

    conn.close()

    # ------------------------------------------------------------------ report
    a, d = out["A_shape"], out["D_concreteness"]
    print("=" * 74)
    print("AEProdSupport playbook corpus - verification")
    print("=" * 74)
    print(f"\nA. SHAPE")
    print(f"   playbooks           {a['playbooks_total']}   lifecycle {a['lifecycle']}")
    print(f"   versions            {a['versions']}   published {a['published_versions']}")
    print(f"   active playbooks    {a['active_playbooks']}")
    print(f"   active steps        {delta(a['active_steps'], POST_REMEDIATION['steps'])}"
          f"   vs post-remediation {POST_REMEDIATION['steps']}")

    b = out["B_integrity"]
    print(f"\nB. INTEGRITY")
    print(f"   broken current_version pointers  {b['broken_current_version_pointers']}")
    print(f"   orphan versions                  {b['orphan_versions']}")
    print(f"   active playbooks with 0 steps    {b['active_playbooks_with_zero_steps']}")

    c = out["C_text_defects"]
    print(f"\nC. RESIDUAL TEXT DEFECTS vs the pre-remediation original")
    print(f"   ticket-verified edits excluded   {c['ticket_verified_edits_excluded']}")
    print(f"   residual defects  {c['residual_defects']}"
          f"{'   <-- REPAIR CONFIRMED' if c['residual_defects'] == 0 else '   <-- STILL BROKEN'}")
    for item in c["detail"][:15]:
        print(f"     [{item['title']}] {item['reasons']}")
        print(f"       was: {item['original'][:150]}")
        print(f"       now: {item['current'][:150]}")

    print(f"\nD. CONCRETENESS  (scorer: {d['scorer']})")
    print(f"   {'band':<10}{'pre-change':>12}{'now':>16}")
    for k in ("0%", "1-33%", "34-66%", "67-100%"):
        was = BASELINE["bands"][k]
        now = d["bands"].get(k, 0)
        print(f"   {k:<10}{was:>12}{now:>10} ({now - was:+d})")
    print(f"   concrete steps   {d['concrete_steps']} of {a['active_steps']}"
          f"  = {d['concrete_pct']}%   (pre-change 49.0%)")
    print(f"   generic/bare-inspect steps  {delta(d['generic_or_bare_inspect_steps'], BASELINE['generic_or_bare_inspect_steps'])}")
    print(f"   hedged steps                {delta(d['hedged_steps'], BASELINE['hedged_steps'])}")

    e = out["E_retrievability"]
    print(f"\nE. RETRIEVABILITY")
    print(f"   playbooks an agent can actually get  {e['agent_retrievable_playbooks']}"
          f"{'   <-- corpus is dark' if e['agent_retrievable_playbooks'] == 0 else ''}")

    f = out["F_step_shape"]
    print(f"\nF. STEP SHAPE")
    print(f"   playbooks at <=2 steps            {f['playbooks_with_two_or_fewer_steps']}")
    print(f"   with an escalation step           {f['playbooks_with_an_escalation_step']}")
    print(f"   with a verification step          {f['playbooks_with_a_verification_step']}")

    OUT_PATH.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print(f"\nwritten: {OUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
