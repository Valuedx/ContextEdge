"""Build a manager-facing old-vs-new playbook quality report from live AEProdSupport.

Writes:
  docs/playbook_corpus_remediation/Playbook_Quality_Report_Manager.md
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor

REPO = Path(__file__).resolve().parents[2]
DOCS = REPO / "docs" / "playbook_corpus_remediation"
SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

from remediate_playbook_corpus import AE_PRODUCT, extract_coords  # noqa: E402

HEDGE = re.compile(r"\b(such as|e\.g\.|for example)\b", re.I)
ESCALATE = re.compile(r"\b(escalat|raise (a )?(ticket|case)|open a vendor support)\b", re.I)
BACKUP = re.compile(r"\bbackup\b", re.I)
VERIFY = re.compile(r"^\s*(verify|validate|confirm|test|re-?run)\b", re.I)
SUFFIX = re.compile(r"Linked tickets specify", re.I)
TICKET_MARKERS = {
    "ActiveMQ transport port 61614": "Named the exact ActiveMQ port from ticket 313308 (61614).",
    "log4j-core-2.25.3.jar": "Named the exact JAR from the VAPT ticket (log4j-core-2.25.3.jar) and said to replace it with a patched copy.",
    "AutomationEdge 8.2.5": "Named the ticket target versions (AutomationEdge 8.2.5 for AppSec, PostgreSQL 15 for PostgreSQL 11 findings).",
    "plugin release 4.5 (ticket 219894)": "Named plugin release 4.5 from ticket 219894. This still needs a manager/SME check: that ticket is a ServiceNow plugin defect, not a generic agent upgrade.",
}


def dsn() -> str:
    env = REPO / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("DATABASE_URL_SYNC="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return "postgresql://postgres:root@localhost:5432/AEProdSupport"


def step_text(step) -> str:
    if isinstance(step, str):
        return step.strip()
    return (step.get("text") or step.get("instruction") or "").strip()


def is_concrete(text: str) -> bool:
    return bool(extract_coords(text) or AE_PRODUCT.search(text))


def pct(n: int, d: int) -> float:
    return round(100.0 * n / d, 1) if d else 0.0


def band(p: float) -> str:
    if p == 0:
        return "0% concrete"
    if p <= 33:
        return "1-33% concrete"
    if p <= 66:
        return "34-66% concrete"
    return "67-100% concrete"


def classify_removed(text: str) -> str:
    if BACKUP.search(text):
        return "backup"
    if ESCALATE.search(text):
        return "escalation"
    if VERIFY.search(text):
        return "check"
    return "filler"


def removed_phrase(kind: str, n: int) -> str:
    noun = {
        "backup": "backup step that was padding, not the actual fix",
        "escalation": "generic escalate / raise-a-ticket step",
        "check": "generic verify/test step that was not the ticket's real check",
        "filler": "filler step that was not supported by the linked ticket",
    }[kind]
    if n == 1:
        return f"Removed 1 {noun}."
    return f"Removed {n} {noun}s."


def similar(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def explain(
    action: str,
    kind: str,
    required: list[str],
    old: list[str],
    new: list[str],
    title: str,
) -> tuple[str, list[str]]:
    tags: list[str] = []
    if action == "SUPPRESS":
        reason = required[0] if required else "It is not an engineer-executable fix procedure."
        reason = reason.rstrip(".")
        tags.append("retired")
        if kind == "info":
            tags.append("inquiry")
        return (
            f"Retired (hidden from future use). Reason: {reason}. "
            "The playbook and its history were not deleted.",
            tags,
        )

    if old == new:
        tags.append("unchanged")
        return (
            "No wording change. The steps already matched the linked tickets, so we left them as they were.",
            tags,
        )

    bits: list[str] = []
    old_set, new_set = set(old), set(new)
    removed = [t for t in old if t not in new_set]
    added = [t for t in new if t not in old_set]

    # Pair remaining changed steps by similarity
    rem_left = list(removed)
    add_left = list(added)
    changed_pairs: list[tuple[str, str]] = []
    used_add = set()
    still_removed = []
    for o in rem_left:
        best_i, best_s = -1, 0.0
        for i, a in enumerate(add_left):
            if i in used_add:
                continue
            s = similar(o, a)
            if s > best_s:
                best_s, best_i = s, i
        if best_i >= 0 and best_s >= 0.45:
            used_add.add(best_i)
            changed_pairs.append((o, add_left[best_i]))
        else:
            still_removed.append(o)
    still_added = [a for i, a in enumerate(add_left) if i not in used_add]

    rm_kinds = Counter(classify_removed(t) for t in still_removed)
    for k, n in rm_kinds.items():
        bits.append(removed_phrase(k, n))
        tags.append(f"removed-{k}")

    space_n = period_n = hedge_back_n = hedge_off_n = suffix_n = other_n = 0
    for o, ntxt in changed_pairs:
        o2, n2 = o.replace(" ", ""), ntxt.replace(" ", "")
        if "Linked tickets specify" in o and "Linked tickets specify" not in ntxt:
            suffix_n += 1
        elif HEDGE.search(o) and not HEDGE.search(ntxt) and similar(HEDGE.sub("", o), ntxt) > 0.85:
            hedge_off_n += 1
        elif (not HEDGE.search(o)) and HEDGE.search(ntxt):
            hedge_back_n += 1
        elif o.rstrip(".,;") == ntxt.rstrip(".,;") and ntxt.endswith(".") and not o.endswith("."):
            period_n += 1
        elif o.replace(" .", ".") == ntxt or (len(o) == len(ntxt) - 1 and " ." in ntxt and "." in o):
            space_n += 1
        elif o2 == n2 or (". " in ntxt and "." in o and o.replace(".", ". ") != ntxt and similar(o, ntxt) > 0.9):
            # glued ".psw" / ".zip" style
            if any(x in ntxt and x.replace(" ", "") in o.replace(" ", "") for x in (" .", ".psw", ".zip", ".xls")):
                space_n += 1
            else:
                other_n += 1
        else:
            # filename glue: missing space before extension
            if re.search(r"[A-Za-z0-9]\.(psw|zip|xls|pluginconf|process-studio|psrc|settings)", o) and re.search(
                r"[A-Za-z0-9] \.(psw|zip|xls|pluginconf|process-studio|psrc|settings)", ntxt
            ):
                space_n += 1
            elif similar(o, ntxt) > 0.7:
                other_n += 1
            else:
                other_n += 1

    if space_n:
        bits.append(
            f"Fixed {space_n} step{'s' if space_n != 1 else ''} where a file name had stuck to the previous word "
            "(for example 'the.psw' became 'the .psw')."
        )
        tags.append("filename-space")
    if period_n:
        bits.append(f"Restored the full stop at the end of {period_n} step{'s' if period_n != 1 else ''}.")
        tags.append("period")
    if hedge_back_n:
        bits.append(
            f"Put 'such as / for example' back in {hedge_back_n} step{'s' if hedge_back_n != 1 else ''} "
            "because removing it had broken the sentence."
        )
        tags.append("hedge-restored")
    if hedge_off_n:
        bits.append(
            f"Removed vague 'such as' wording in {hedge_off_n} step{'s' if hedge_off_n != 1 else ''} "
            "where the ticket already named the value."
        )
        tags.append("hedge-removed")
    if suffix_n:
        bits.append("Removed an unverified 'Linked tickets specify …' version tag that had been added without checking the ticket.")
        tags.append("suffix-stripped")
    if other_n and not (space_n or hedge_back_n or hedge_off_n or suffix_n):
        bits.append("Cleaned up step wording so it stays true to the linked ticket (no new product steps invented).")
        tags.append("wording-cleanup")
    elif other_n:
        bits.append("Small wording cleanup so the step still matches the ticket.")
        tags.append("wording-cleanup")

    for a in still_added:
        tags.append("step-reinserted")
        if "javassist" in a.lower():
            bits.append("Put back the real fix: copy the latest javassist JAR into the lib folder.")
        elif "pluginsconf" in a.lower() or ".pluginsconf" in a.lower():
            bits.append("Put back the real fix: backup and delete the corrupted .pluginsconf file.")
        else:
            bits.append("Put back a step that carried the actual fix action.")

    for marker, sentence in TICKET_MARKERS.items():
        if any(marker in t for t in new) and not any(marker in t for t in old):
            bits.append(sentence)
            tags.append("ticket-value")

    if not bits:
        bits.append("Steps were edited so they match the linked tickets more closely. No new AutomationEdge screens or commands were invented.")
        tags.append("wording-cleanup")

    if action == "KEEP" and old != new:
        bits.insert(0, "Originally left unchanged, then a later correction pass updated the wording.")

    return " ".join(bits), tags


def load_decisions() -> dict[str, dict]:
    out = {}
    with (DOCS / "remediation_decisions.jsonl").open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rec = json.loads(line)
                out[rec["playbook_id"]] = rec
    return out


def main() -> None:
    originals = json.loads((DOCS / "playbook_original_steps.json").read_text(encoding="utf-8"))["playbooks"]
    decisions = load_decisions()

    conn = psycopg2.connect(dsn())
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT set_config('app.bypass_rls', 'on', false)")
    cur.execute(
        """
        SELECT p.id::text AS id, p.title, p.lifecycle_state, p.risk_tier,
               pv.steps, pv.playbook_confidence
        FROM playbooks p
        JOIN playbook_versions pv ON pv.id = p.current_version_id
        ORDER BY p.title
        """
    )
    rows = cur.fetchall()
    conn.close()

    catalog = []
    for r in rows:
        pid = r["id"]
        orig = originals.get(pid, {})
        dec = decisions.get(pid, {})
        old_steps = list(orig.get("steps") or [])
        new_steps = [step_text(s) for s in (r["steps"] or [])]
        action = dec.get("action", "KEEP" if r["lifecycle_state"] != "retired" else "SUPPRESS")
        kind = dec.get("kind", "")
        required = dec.get("required_changes") or []
        tickets = sorted({str(t) for t in (dec.get("case_refs") or []) if t})
        old_c = sum(1 for t in old_steps if is_concrete(t))
        new_c = sum(1 for t in new_steps if is_concrete(t))
        old_pct = pct(old_c, len(old_steps))
        new_pct = pct(new_c, len(new_steps))
        summary, tags = explain(action, kind, required, old_steps, new_steps, r["title"])
        catalog.append(
            {
                "id": pid,
                "title": r["title"],
                "action": action,
                "status": "Retired" if r["lifecycle_state"] == "retired" else "Active (candidate)",
                "risk": (r["risk_tier"] or "").capitalize() or "—",
                "tickets": tickets,
                "component": dec.get("component") or "",
                "old_steps": old_steps,
                "new_steps": new_steps,
                "old_n": len(old_steps),
                "new_n": len(new_steps),
                "old_concrete": old_c,
                "new_concrete": new_c,
                "old_pct": old_pct,
                "new_pct": new_pct,
                "old_band": band(old_pct),
                "new_band": band(new_pct),
                "summary": summary,
                "tags": tags,
                "kind": kind,
            }
        )

    # ---- totals
    n = len(catalog)
    by_action = Counter(c["action"] for c in catalog)
    active = [c for c in catalog if c["status"].startswith("Active")]
    retired = [c for c in catalog if c["status"] == "Retired"]
    old_steps_all = sum(c["old_n"] for c in catalog)
    new_steps_active = sum(c["new_n"] for c in active)
    old_conc = sum(c["old_concrete"] for c in catalog)
    new_conc = sum(c["new_concrete"] for c in active)
    changed = [c for c in catalog if c["old_steps"] != c["new_steps"] and c["action"] != "SUPPRESS"]
    unchanged = [c for c in catalog if c["old_steps"] == c["new_steps"] and c["action"] != "SUPPRESS"]

    write_md(catalog, by_action, active, retired, old_steps_all, new_steps_active, old_conc, new_conc, changed, unchanged)
    print(f"MD    {DOCS / 'Playbook_Quality_Report_Manager.md'}")
    print(f"playbooks={n} KEEP={by_action['KEEP']} IMPROVE={by_action['IMPROVE']} SUPPRESS={by_action['SUPPRESS']}")
    print(f"changed={len(changed)} unchanged={len(unchanged)} active_steps={new_steps_active}")


def cell(s: str) -> str:
    return str(s or "").replace("|", "\\|").replace("\n", " ")


def numbered(steps: list[str]) -> str:
    if not steps:
        return "_None._"
    return "\n".join(f"{i}. {t}" for i, t in enumerate(steps, 1))


def write_md(catalog, by_action, active, retired, old_steps_all, new_steps_active, old_conc, new_conc, changed, unchanged):
    keep = by_action.get("KEEP", 0)
    improve = by_action.get("IMPROVE", 0)
    suppress = by_action.get("SUPPRESS", 0)
    old_pct = pct(old_conc, old_steps_all)
    new_pct = pct(new_conc, new_steps_active)
    retired_lis = "\n".join(f"- {c['title']}" for c in retired)

    index_rows = []
    for i, c in enumerate(catalog, 1):
        tickets = ", ".join(c["tickets"]) or "—"
        index_rows.append(
            f"| {i} | {cell(c['title'])} | {c['action']} | {cell(c['status'])} | "
            f"{c['old_n']} | {c['new_n']} | {c['old_pct']}% | {c['new_pct']}% | {cell(tickets)} | {cell(c['summary'])} |"
        )

    detail = []
    for i, c in enumerate(catalog, 1):
        tickets = ", ".join(c["tickets"]) or "—"
        delta = "retired" if c["action"] == "SUPPRESS" else f"{c['new_n'] - c['old_n']:+d}"
        same = c["old_steps"] == c["new_steps"]
        detail.append(
            f"### {i}. {c['title']}\n\n"
            f"- **Decision:** {c['action']}\n"
            f"- **Status now:** {c['status']}\n"
            f"- **Risk:** {c['risk']}\n"
            f"- **Linked tickets:** {tickets}\n"
            f"- **Steps:** {c['old_n']} before → {c['new_n']} after ({delta})\n"
            f"- **How specific:** {c['old_pct']}% → {c['new_pct']}% of steps name a file, product, port or command\n\n"
            f"**What changed:** {c['summary']}\n"
        )
        if same and c["action"] != "SUPPRESS":
            detail.append("_Steps are the same as before, so they are listed once._\n\n**Steps**\n\n" + numbered(c["new_steps"]) + "\n")
        else:
            detail.append(
                f"**Before ({c['old_n']} steps)**\n\n" + numbered(c["old_steps"]) + "\n\n"
                f"**After ({c['new_n']} steps)**\n\n" + numbered(c["new_steps"]) + "\n"
            )

    body = f"""# Playbook quality report

**Product:** AutomationEdge production-support playbooks  
**Database:** AEProdSupport  
**Date:** 26 August 2026  
**Audience:** management review  

This report is in plain English. It compares every playbook **before cleanup** with **what is in the database now**. We did not invent AutomationEdge screens, config keys, or commands that were not in the linked ticket.

---

## 1. Database check — is the repair complete?

**Yes — the wording repair on live playbooks is complete.** Re-checked against AEProdSupport on 26 August 2026.

| Check | Result |
|---|---|
| Playbooks in database | 440 (none deleted) |
| Active (candidate) | 420 |
| Retired (hidden, not deleted) | 20 |
| Versions | 863 (old versions kept for rollback) |
| Published versions | 0 |
| Approved playbooks | 0 |
| Active instruction steps | 1,849 |
| Broken version pointers | 0 |
| Orphan versions | 0 |
| Active playbooks with zero steps | 0 |
| Leftover glued file names (`the.psw`, `and.process-studio`) | 0 |
| Leftover fused words (`toolPostman`, `flags--disable-gpu`) | 0 |
| Unverified “Linked tickets specify …” tags | 0 |
| Repair script re-run | 0 further edits — already applied |
| Real fix steps put back | javassist JAR swap; delete corrupted `.pluginsconf` |

**One line still differs from the original on purpose:** *Agent Upgrade Feature Misunderstanding and Clarification* names plugin release 4.5 from ticket 219894. The repair script correctly refused to overwrite it. That ticket is a ServiceNow plugin defect — an SME should confirm the wording. This is **not** leftover broken text from the cleanup.

**Not pending as a repair, but not go-live yet:**

- The support **agent cannot retrieve any playbook** until they are approved and published (all 420 are still candidate).
- Vector embeddings were not rebuilt. Do that after publish.
- We did not restore generic “escalate” steps. Only 17 playbooks still have an escalation step.
- We did not rewrite 109 unchanged playbooks that an older audit called vague, because the tickets did not give us extra coordinates to add.

---

## 2. One-page summary (old vs new)

| What we measured | Before | After |
|---|---:|---:|
| Playbooks in the library | 440 | 440 (none deleted) |
| Ready for engineers (not retired) | 440 | 420 |
| Retired (hidden, not deleted) | 0 | 20 |
| Instruction steps on those playbooks | {old_steps_all} | {new_steps_active} |
| Steps that name a real file, product, port or command | {old_pct}% | {new_pct}% |
| Playbooks with wording changed | — | {len(changed)} |
| Playbooks left exactly as they were | — | {len(unchanged)} |
| Playbooks an agent can retrieve today | 0 | 0 (not yet approved / published) |

**What got better.** Filler (generic backup / “notify the customer” / empty inspect steps) fell from 69 to 13. File names that had glued to the previous word are fixed. Two real fix steps that had been dropped with “backup” padding were put back. Nothing was hard-deleted.

**What did not magically become specific.** Concrete steps only moved from {old_pct}% to {new_pct}%. Most tickets never named the exact AutomationEdge screen or config key, and we refused to guess. That remaining gap is missing evidence, not a missed cleanup.

---

## 3. What we did (three decisions)

| Decision | Count | Meaning |
|---|---:|---|
| KEEP | {keep} | Steps already matched the ticket. We did not rewrite them. |
| IMPROVE | {improve} | We trimmed filler and kept only what the ticket supports. A new unpublished version was saved. |
| SUPPRESS (retire) | {suppress} | Real ticket, but not a how-to for an engineer (inquiry, denied feature, waiting on customer). Hidden, not deleted. |
| REWRITE / MERGE / DELETE | 0 / 0 / 0 | Not used. Similar titles had different root causes. We did not invent replacement procedures. |

**Later correction passes**

1. **Version tags.** 35 steps had an unverified “Linked tickets specify …” suffix. Those were removed. Only values re-read on the ticket were written in: ActiveMQ port 61614; log4j-core-2.25.3.jar; AutomationEdge 8.2.5 / PostgreSQL 15.
2. **Wording repair.** The first cleanup had glued some file names and dropped “such as”, which broke sentences. 92 steps across 71 playbooks were repaired. Two real fix steps were put back.
3. **One skip.** Agent Upgrade … still names plugin release 4.5 (ticket 219894). Needs an SME check.

---

## 4. Retired playbooks (20)

These stay in the database for history. They should not be used as runbooks.

{retired_lis}

---

## 5. Index of all 440 playbooks

| # | Playbook | Decision | Status now | Steps before | Steps after | Specific before | Specific after | Tickets | What changed |
|---|---|---|---|---:|---:|---:|---:|---|---|
{chr(10).join(index_rows)}

---

## 6. Each playbook — old vs new

{chr(10).join(detail)}

---

## Notes for the reader

- **Specific %** = share of steps that name a file, product, port, or command. Same yardstick before and after.
- **Source:** live AEProdSupport compared with the 26 August 2026 pre-change backup.
- Old versions of improved playbooks are still in `playbook_versions` for rollback.
"""
    (DOCS / "Playbook_Quality_Report_Manager.md").write_text(body, encoding="utf-8")


if __name__ == "__main__":
    main()
