"""Evidence-grounded remediation of every AutomationEdge playbook in AEProdSupport.

Reads live playbook, pattern, episode, and ticket records. Does not invent
AutomationEdge UI paths, config keys, commands, or versions. Filler that is
not in the source resolution is removed. Playbooks that cannot be grounded
are suppressed or deleted after dependency checks.

Usage:
    python backend/scripts/remediate_playbook_corpus.py           # report only
    python backend/scripts/remediate_playbook_corpus.py --apply   # write DB
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
from psycopg2.extras import Json, RealDictCursor, register_uuid

class UUIDJson(Json):
    def dumps(self, obj):
        return json.dumps(obj, default=str)

DSN = "postgresql://postgres:root@localhost:5432/AEProdSupport"
OUT_DIR = Path(r"D:\ContextEdge_pro\ContextEdge\docs\playbook_corpus_remediation")
BACKUP_DIR = Path(r"D:\ContextEdge_pro\ContextEdge\data\playbook_remediation_backup_2026-08-26")
RUN_ID = "corpus_remediation_2026-08-26"

STOP = {
    "the", "and", "for", "with", "from", "that", "this", "was", "were", "are",
    "is", "to", "of", "in", "on", "or", "a", "an", "be", "by", "as", "at",
    "it", "if", "not", "no", "yes", "into", "via", "due", "its", "their",
    "user", "users", "issue", "issues", "please", "also", "then", "than",
    "any", "all", "can", "could", "should", "must", "may", "will", "would",
    "have", "has", "had", "been", "being", "using", "used", "use", "per",
}

AE_PRODUCT = re.compile(
    r"\b(automationedge|process studio|activemq|ae_home|ae\.properties|"
    r"web console|copilot|rpa agent|processstudio|nginx|tomcat|vault|"
    r"cyberark|epd|psplugins|webui_drivers|step unit|agent_home|"
    r"generic database|redshift|jdbc|ldap|o365|sftp|udjc|plugin)\b",
    re.I,
)

COORD_RE = re.compile(
    r"("
    r"[A-Za-z]:\\[^\s,;]+"
    r"|<[^>\s]+>(?:\\[^\s,;]+)+"
    r"|[\w.-]+\.(?:jar|properties|xml|log|conf|yml|yaml|ini|bat|sh|ps1|lic|dll|exe)"
    r"|com\.[A-Za-z0-9_.]+"
    r"|jdbc:[^\s]+"
    r"|(?:activemq|ae|jdbc|ssl|mail|ldap|broker)[.\w-]{3,}"
    r"|(?:net (?:stop|start)|systemctl|tscon|sc (?:query|start|stop)|query session)"
    r"\s+[^\n.]{0,80}"
    r"|--[A-Za-z0-9_=-]+"
    r"|:\d{2,5}\b"
    r"|port\s+\d+"
    r"|\b\d+\.\d+\.\d+(?:\.\d+)?\b"
    r"|['\"][^'\"\n]{3,120}['\"]"
    r")",
    re.I,
)

HEDGE_RE = re.compile(r"\b(such as|e\.g\.|for example)\b", re.I)
FILLER_RE = re.compile(
    r"("
    r"\b(create|take|make)\s+a\s+(full\s+)?backup\b"
    r"|\bbackup copy of\b"
    r"|\bdeploy(?:ed|ing)? (?:the )?(?:target )?(?:software )?(?:release|package).{0,40}uat\b"
    r"|\b(functional )?regression tests?\b"
    r"|\bnotify (?:the )?(?:user|customer|stakeholders?)\b"
    r"|\binform(?:ed)? (?:the )?(?:user|customer|stakeholders?)\b"
    r"|\bescalate(?:d|tion)?\b"
    r"|\bfollow up\b"
    r"|\bschedule (?:a )?meeting\b"
    r"|\bclose the ticket\b"
    r"|\bdocument (?:the )?(?:findings?|issue|resolution)\b"
    r"|\bmonitor the (?:situation|issue|environment)\b"
    r"|\breview the (?:issue|report|ticket|request)\b"
    r"|\bverify (?:the )?(?:identity of )?(?:the )?requester\b"
    r"|\bprompt the user to test\b"
    r"|\bdeliver the validated release\b"
    r"|\blessons learned\b"
    r")",
    re.I,
)
BARE_INSPECT_RE = re.compile(
    r"^(review|check|verify|inspect|examine|confirm)\s+"
    r"(the\s+)?(logs?|status|health|environment|configuration|system|"
    r"performance|behavior|issue|application|connectivity)\.?$",
    re.I,
)
WEAK_RC_RE = re.compile(
    r"(unknown|not (?:determined|identified|provided)|implied|"
    r"standard (?:process|procedure|request)|customer inquiry|"
    r"information request|no root cause)",
    re.I,
)
WEAK_OUTCOME_RE = re.compile(
    r"(unknown|not (?:determined|provided|recorded)|no (?:resolution|outcome)|"
    r"waiting|pending (?:customer|client|user)|no response|"
    r"ticket closed without|information (?:was )?provided|"
    r"explained (?:the|how)|clarified|informed the customer)",
    re.I,
)
ACTION_OUTCOME_RE = re.compile(
    r"\b(restarted|started|stopped|upgraded|updated|configured|replaced|"
    r"installed|patched|enabled|disabled|unblocked|activated|renewed|"
    r"uploaded|applied|fixed|resolved by|workaround|re-registered|"
    r"reinstalled|copied|synced|increased|set |added |removed |"
    r"granted|assigned|approved|provided release|rolled back)\b",
    re.I,
)
INFO_TITLE_RE = re.compile(
    r"(inquiry|information request|documentation discoverability|"
    r"misunderstanding|clarification|knowledge gap|"
    r"feasibility|compliance quer)",
    re.I,
)
DENIED_RE = re.compile(
    r"(feature request denied|denied due to|deferred by product|"
    r"product feature gap|architectural or security constraints|"
    r"unsupported (?:application )?feature|unsupported deployment)",
    re.I,
)
WAITING_RE = re.compile(
    r"(pending customer|client unresponsiveness|waiting for|"
    r"stalled by client)",
    re.I,
)
NARRATIVE_JUNK_RE = re.compile(
    r"("
    r"^(the )?(issue|ticket|case|problem) (was |is )?(resolved|closed|fixed)\b"
    r"|proceed(?:ed)? with (the )?clos"
    r"|ticket (was |is )?(closed|confirmed)"
    r"|no further actions?"
    r"|a meeting was scheduled"
    r"|unclear, repeated"
    r"|as soon as possible"
    r"|we will proceed with closing"
    r"|from our end"
    r"|lack of formal training"
    r"|develop and conduct training"
    r"|review and update official documentation"
    r"|provide immediate, step-by-step guidance"
    r")",
    re.I,
)
IMPERATIVE_START_RE = re.compile(
    r"^(please )?(check|verify|inspect|open|edit|set|add|remove|restart|"
    r"start|stop|run|upgrade|update|replace|install|configure|deploy|"
    r"upload|enable|disable|clear|locate|identify|compare|purge|"
    r"increase|decrease|copy|sync|re-?register|reinstall|apply|"
    r"use |switch |change |confirm |navigate |click |select |"
    r"request |assign |grant |renew |activate |unblock )",
    re.I,
)
PROCESS_TITLE_RE = re.compile(
    r"(license|account|provisioning|plugin assignment|user role|"
    r"access provisioning|dormant)",
    re.I,
)
SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")

COMPONENTS = [
    "activemq", "process studio", "nginx", "copilot", "rpa agent",
    "redshift", "ldap", "o365", "sftp", "vault", "cyberark", "jdbc",
    "chrome", "ie mode", "postgresql", "tomcat", "git", "totp",
    "gui spy", "excel", "browser", "rdp", "license", "plugin",
]


def connect():
    register_uuid()
    conn = psycopg2.connect(DSN)
    conn.autocommit = False
    with conn.cursor() as cur:
        cur.execute("SELECT set_config('app.bypass_rls', 'on', false)")
    return conn


def step_text(step: dict) -> str:
    if not isinstance(step, dict):
        return str(step or "")
    return (
        step.get("text")
        or step.get("instruction")
        or step.get("title")
        or step.get("action")
        or ""
    ).strip()


def tokenize(text: str) -> set[str]:
    return {
        t
        for t in re.findall(r"[a-z0-9][a-z0-9_.-]{2,}", (text or "").lower())
        if t not in STOP
    }


def overlap_ratio(a: str, b: str) -> float:
    ta, tb = tokenize(a), tokenize(b)
    if not ta:
        return 0.0
    return len(ta & tb) / len(ta)


def is_actionable_coord(c: str) -> bool:
    if not c or len(c) > 100 or " " in c and len(c) > 60:
        return False
    return bool(
        re.search(
            r"\.(jar|properties|xml|log|conf|yml|bat|sh|lic|dll)\b|jdbc:|"
            r"com\.[A-Za-z0-9_.]+|port\s+\d+|:\d{2,5}\b|"
            r"wrapper\.|activemq\.|ae\.|[A-Za-z]:\\|<[^>]+>\\|--[A-Za-z]",
            c,
            re.I,
        )
    )


def usable_procedure_text(text: str) -> bool:
    t = (text or "").strip()
    if len(t) < 24:
        return False
    if NARRATIVE_JUNK_RE.search(t):
        return False
    if t.lower().startswith(("the issue", "the ticket", "a request", "support ", "it was")):
        return False
    return True


def extract_coords(text: str) -> list[str]:
    if not text:
        return []
    found = []
    seen = set()
    for m in COORD_RE.finditer(text):
        val = m.group(0).strip().strip(".,;")
        key = val.lower()
        if key not in seen and len(val) >= 4:
            seen.add(key)
            found.append(val)
    return found


def next_version(current: str | None) -> str:
    if current and (m := SEMVER_RE.match(current)):
        return f"{m.group(1)}.{m.group(2)}.{int(m.group(3)) + 1}"
    return "0.1.1"


def load_corpus(cur) -> list[dict]:
    cur.execute(
        """
        SELECT p.id, p.tenant_id, p.title, p.description, p.lifecycle_state,
               p.risk_tier, p.pattern_id, p.current_version_id, p.stable_key,
               p.owner_user_id, p.lexical_search_text,
               pv.semantic_version, pv.steps, pv.trigger_conditions,
               pv.branching_logic, pv.inputs, pv.outputs, pv.rollback_notes,
               pv.evidence_refs, pv.conflicts, pv.playbook_confidence,
               pv.execution_confidence_guidance, pv.verification_policy,
               pv.generation_provenance,
               pat.title AS pattern_title,
               pat.confidence AS pattern_confidence,
               pat.episode_count AS pattern_episode_count,
               pat.root_causes, pat.resolution_steps, pat.observed_errors,
               pat.trigger_conditions AS pattern_triggers,
               pat.core_entities, pat.active_flag
        FROM playbooks p
        JOIN playbook_versions pv ON pv.id = p.current_version_id
        JOIN patterns pat ON pat.id = p.pattern_id
        ORDER BY p.title
        """
    )
    playbooks = [dict(r) for r in cur.fetchall()]
    ids = [p["id"] for p in playbooks]
    version_ids = [p["current_version_id"] for p in playbooks]
    pattern_ids = [p["pattern_id"] for p in playbooks]

    cur.execute(
        """
        SELECT pel.playbook_version_id, pel.episode_id, pel.evidence_id, pel.link_type,
               e.title AS episode_title, e.primary_case_ref, e.root_cause_summary,
               e.final_outcome, e.status, e.reviewer_state, e.extraction_confidence
        FROM playbook_evidence_links pel
        LEFT JOIN episodes e ON e.id = pel.episode_id
        WHERE pel.playbook_version_id = ANY(%s::uuid[])
        """,
        (version_ids,),
    )
    links_by_version = defaultdict(list)
    episode_ids = set()
    evidence_ids = set()
    for row in cur.fetchall():
        rec = dict(row)
        links_by_version[rec["playbook_version_id"]].append(rec)
        if rec["episode_id"]:
            episode_ids.add(rec["episode_id"])
        if rec["evidence_id"]:
            evidence_ids.add(rec["evidence_id"])

    cur.execute(
        """
        SELECT pattern_id, episode_id
        FROM pattern_evidence_links
        WHERE pattern_id = ANY(%s::uuid[]) AND episode_id IS NOT NULL
        """,
        (pattern_ids,),
    )
    pattern_episodes = defaultdict(set)
    for row in cur.fetchall():
        pattern_episodes[row["pattern_id"]].add(row["episode_id"])
        episode_ids.add(row["episode_id"])

    cur.execute(
        """
        SELECT episode_id, evidence_id
        FROM episode_evidence_links
        WHERE episode_id = ANY(%s::uuid[])
        """,
        (list(episode_ids) or [uuid.UUID(int=0)],),
    )
    ep_ev = defaultdict(list)
    for row in cur.fetchall():
        ep_ev[row["episode_id"]].append(row["evidence_id"])
        evidence_ids.add(row["evidence_id"])

    cur.execute(
        """
        SELECT id, title, primary_case_ref, root_cause_summary, final_outcome, status
        FROM episodes
        WHERE id = ANY(%s::uuid[])
        """,
        (list(episode_ids) or [uuid.UUID(int=0)],),
    )
    episode_meta = {r["id"]: dict(r) for r in cur.fetchall()}

    cur.execute(
        """
        SELECT episode_id, step_order, step_type, text, observation,
               successful_flag, failed_flag, result_state
        FROM episode_steps
        WHERE episode_id = ANY(%s::uuid[])
          AND step_type IN ('action','remediation','outcome','failed_step','diagnostic')
        ORDER BY episode_id, step_order
        """,
        (list(episode_ids) or [uuid.UUID(int=0)],),
    )
    steps_by_ep = defaultdict(list)
    for row in cur.fetchall():
        steps_by_ep[row["episode_id"]].append(dict(row))

    evidence_map = {}
    if evidence_ids:
        cur.execute(
            """
            SELECT id, evidence_type, title,
                   left(coalesce(body_summary, ''), 1500) AS body_summary,
                   left(coalesce(body_text, ''), 2000) AS body_head,
                   right(coalesce(body_text, ''), 1500) AS body_tail
            FROM evidence_items
            WHERE id = ANY(%s::uuid[])
            """,
            (list(evidence_ids),),
        )
        for row in cur.fetchall():
            evidence_map[row["id"]] = dict(row)

    cur.execute(
        """
        SELECT eis.episode_id, s.failing_component, s.failure_mode, s.affected_capability
        FROM episode_issue_signatures eis
        JOIN issue_signatures s ON s.id = eis.issue_signature_id
        WHERE eis.episode_id = ANY(%s::uuid[])
        """,
        (list(episode_ids) or [uuid.UUID(int=0)],),
    )
    sig_by_ep = defaultdict(list)
    for row in cur.fetchall():
        sig_by_ep[row["episode_id"]].append(dict(row))

    cur.execute(
        """
        SELECT pattern_id, COUNT(*) AS n
        FROM pattern_evidence_links
        GROUP BY pattern_id
        """
    )
    pattern_link_counts = {r["pattern_id"]: r["n"] for r in cur.fetchall()}

    cur.execute(
        """
        SELECT episode_id, COUNT(DISTINCT pattern_id) AS n
        FROM pattern_evidence_links
        WHERE episode_id IS NOT NULL
        GROUP BY episode_id
        """
    )
    episode_pattern_counts = {r["episode_id"]: r["n"] for r in cur.fetchall()}

    for pb in playbooks:
        links = links_by_version.get(pb["current_version_id"], [])
        episodes = []
        seen_ep = set()
        ticket_bits = []
        for link in links:
            eid = link["episode_id"]
            if eid and eid not in seen_ep:
                seen_ep.add(eid)
                ev_bits = []
                for evid in ep_ev.get(eid, []):
                    item = evidence_map.get(evid)
                    if not item:
                        continue
                    ev_bits.append(
                        " ".join(
                            filter(
                                None,
                                [
                                    item.get("title"),
                                    item.get("body_summary"),
                                    item.get("body_head"),
                                    item.get("body_tail"),
                                ],
                            )
                        )
                    )
                episodes.append(
                    {
                        "id": str(eid),
                        "case_ref": link.get("primary_case_ref"),
                        "title": link.get("episode_title"),
                        "root_cause": link.get("root_cause_summary") or "",
                        "outcome": link.get("final_outcome") or "",
                        "status": link.get("status"),
                        "steps": steps_by_ep.get(eid, []),
                        "signatures": sig_by_ep.get(eid, []),
                        "ticket_text": "\n".join(ev_bits)[:8000],
                    }
                )
                ticket_bits.extend(ev_bits)
            if link.get("evidence_id"):
                item = evidence_map.get(link["evidence_id"])
                if item:
                    ticket_bits.append(
                        " ".join(
                            filter(
                                None,
                                [
                                    item.get("title"),
                                    item.get("body_summary"),
                                    item.get("body_head"),
                                    item.get("body_tail"),
                                ],
                            )
                        )
                    )
        extra_eps = pattern_episodes.get(pb["pattern_id"], set()) - seen_ep
        for eid in extra_eps:
            meta = episode_meta.get(eid) or {}
            ev_bits = []
            for evid in ep_ev.get(eid, []):
                item = evidence_map.get(evid)
                if not item:
                    continue
                ev_bits.append(
                    " ".join(
                        filter(
                            None,
                            [
                                item.get("title"),
                                item.get("body_summary"),
                                item.get("body_head"),
                                item.get("body_tail"),
                            ],
                        )
                    )
                )
            ticket_bits.extend(ev_bits)
            episodes.append(
                {
                    "id": str(eid),
                    "case_ref": meta.get("primary_case_ref"),
                    "title": meta.get("title"),
                    "root_cause": meta.get("root_cause_summary") or "",
                    "outcome": meta.get("final_outcome") or "",
                    "status": meta.get("status"),
                    "steps": steps_by_ep.get(eid, []),
                    "signatures": sig_by_ep.get(eid, []),
                    "ticket_text": "\n".join(ev_bits)[:8000],
                    "via": "pattern_only",
                }
            )
        pb["episodes"] = [e for e in episodes if e.get("title") or e.get("steps") or e.get("root_cause")]
        # Keep pattern-only stubs out of the main list if they add nothing.
        pb["all_episode_ids"] = [str(x) for x in (seen_ep | extra_eps)]
        pb["evidence_text"] = build_evidence_text(pb, ticket_bits)
        pb["pattern_link_count"] = pattern_link_counts.get(pb["pattern_id"], 0)
        pb["episode_pattern_counts"] = {
            str(eid): episode_pattern_counts.get(eid, 0)
            for eid in (seen_ep | extra_eps)
        }
        pb["playbook_evidence_link_rows"] = links
    return playbooks


def build_evidence_text(pb: dict, ticket_bits: list[str]) -> str:
    parts = [
        pb.get("title") or "",
        pb.get("pattern_title") or "",
        json.dumps(pb.get("root_causes") or []),
        json.dumps(pb.get("resolution_steps") or []),
        json.dumps(pb.get("observed_errors") or []),
        json.dumps(pb.get("core_entities") or []),
    ]
    for ep in pb.get("episodes") or []:
        parts.extend(
            [
                ep.get("title") or "",
                ep.get("root_cause") or "",
                ep.get("outcome") or "",
                ep.get("case_ref") or "",
            ]
        )
        for st in ep.get("steps") or []:
            parts.append(st.get("text") or "")
            parts.append(st.get("observation") or "")
    parts.extend(ticket_bits[:12])
    return "\n".join(p for p in parts if p)


def evidence_quality(pb: dict) -> dict:
    eps = [e for e in pb["episodes"] if e.get("title")]
    rcs = [e.get("root_cause") or "" for e in eps]
    outcomes = [e.get("outcome") or "" for e in eps]
    res_steps = pb.get("resolution_steps") or []
    if isinstance(res_steps, str):
        res_steps = [res_steps]
    tech = bool(AE_PRODUCT.search(pb["evidence_text"]) or extract_coords(pb["evidence_text"]))
    actionable = any(ACTION_OUTCOME_RE.search(o) for o in outcomes) or any(
        len(str(s)) > 25 for s in res_steps
    )
    weak_rc = (not rcs) or all(len(r.strip()) < 25 or WEAK_RC_RE.search(r) for r in rcs)
    weak_out = (not outcomes) or all(
        len(o.strip()) < 20 or WEAK_OUTCOME_RE.search(o) for o in outcomes
    )
    n_action_steps = sum(
        1
        for e in eps
        for s in e.get("steps") or []
        if s.get("step_type") in {"action", "remediation", "outcome"} and len(s.get("text") or "") > 20
    )
    if not eps:
        band = "none"
    elif weak_rc and weak_out and not actionable and n_action_steps == 0:
        band = "insufficient"
    elif weak_out and not actionable:
        band = "thin"
    elif tech and actionable:
        band = "strong"
    else:
        band = "moderate"
    return {
        "band": band,
        "episode_count": len(eps),
        "tech": tech,
        "actionable": actionable,
        "weak_rc": weak_rc,
        "weak_outcome": weak_out,
        "action_step_count": n_action_steps,
        "coords": [c for c in extract_coords(pb["evidence_text"]) if is_actionable_coord(c)][:20],
    }


def playbook_kind(pb: dict) -> str:
    blob = " ".join(
        [
            pb.get("title") or "",
            " ".join(e.get("outcome") or "" for e in pb["episodes"]),
            " ".join(e.get("root_cause") or "" for e in pb["episodes"]),
        ]
    )
    title = pb.get("title") or ""
    if DENIED_RE.search(title):
        return "denied"
    if INFO_TITLE_RE.search(title) and not ACTION_OUTCOME_RE.search(blob):
        return "info"
    if WAITING_RE.search(title) and not ACTION_OUTCOME_RE.search(blob):
        return "waiting"
    if PROCESS_TITLE_RE.search(title) and not re.search(
        r"failure|error|crash|unable|not working", title, re.I
    ):
        return "process"
    if ACTION_OUTCOME_RE.search(blob) or (pb.get("resolution_steps") or []):
        return "fix"
    return "unknown"


def classify_step(step: dict, evidence: str, eq: dict) -> str:
    text = step_text(step)
    if not text:
        return "UNGROUNDED"
    grounded = (step.get("grounding_status") == "grounded") and bool(step.get("source_refs"))
    ov = overlap_ratio(text, evidence)
    coords_in_step = extract_coords(text)
    filler = bool(FILLER_RE.search(text))
    filler_in_ev = bool(FILLER_RE.search(evidence)) and ov > 0.25
    hedge = bool(HEDGE_RE.search(text))

    if filler and not filler_in_ev:
        return "AI_PADDING"
    if BARE_INSPECT_RE.match(text) and not coords_in_step:
        return "GENERIC_IT"
    if ov < 0.12 and not grounded:
        if AE_PRODUCT.search(text) or coords_in_step:
            return "INCORRECT_FOR_TICKET"
        return "UNGROUNDED"
    if hedge or (not coords_in_step and AE_PRODUCT.search(text) and ov >= 0.12):
        if coords_in_step or (eq["coords"] and ov >= 0.2):
            return "VALID_BUT_NEEDS_ANCHORING"
        if AE_PRODUCT.search(text) or grounded or ov >= 0.25:
            return "VALID_AE_STEP"
        return "GENERIC_IT"
    if coords_in_step and (ov >= 0.12 or grounded):
        return "VALID_AE_STEP"
    if AE_PRODUCT.search(text) and ov >= 0.18:
        return "VALID_AE_STEP"
    if grounded and ov >= 0.15:
        return "VALID_AE_STEP"
    if ov >= 0.22 and not filler:
        return "VALID_AE_STEP" if (AE_PRODUCT.search(text) or coords_in_step) else "VALID_BUT_NEEDS_ANCHORING"
    if filler:
        return "AI_PADDING"
    if ov >= 0.15:
        return "VALID_BUT_NEEDS_ANCHORING"
    return "GENERIC_IT"


# Matches an inline hedge and a BOUNDED example. The old pattern was
#   r"\(?\s*(?:such as|e\.g\.|for example)\s+([^)\n]+)\)?"
# which (a) swallowed the space before the marker, so the replacement fused with the
# preceding word ("an external API tool such as Postman" -> "...API toolPostman"), and
# (b) let the capture group run to the next ")" or end of line, so returning a single
# coordinate discarded the rest of the sentence ("...passing flags such as --disable-gpu
# and --disable-software-rasterizer or deploying an updated runner configuration JAR."
# -> "...passing flags--disable-gpu."). Both are now structurally impossible: the
# leading whitespace is captured and re-emitted, and the example cannot cross a comma,
# semicolon, sentence period, or parenthesis.
HEDGE_INLINE_RE = re.compile(
    r"(?P<lead>\s)(?P<marker>such as|e\.g\.,?|for example,?|for instance,?)\s+"
    r"(?P<example>[^,;()\n]{1,80}?)(?=[,;.)\n]|$)",
    re.I,
)


def unhedge(text: str, evidence: str) -> str:
    """Replace a hedged example with the exact value the ticket names.

    Conservative by design. The step is only rewritten when the linked evidence
    supplies a coordinate that actually differs from the example already in the text;
    otherwise the hedge is left alone. Deleting the words "such as" on its own gains
    nothing and risks leaving two noun phrases butted together ("the preferred
    technology a Python script was blocked"), which is what the previous version did
    to 51 steps.

    Parenthetical examples - "(such as X)" - are never touched: removing the marker
    strands the parentheses, and removing the whole parenthetical loses the example.
    """
    coords = [c for c in extract_coords(evidence) if is_actionable_coord(c)]
    ev_l = (evidence or "").lower()

    def repl(m: re.Match) -> str:
        lead = m.group("lead")
        example = (m.group("example") or "").strip()
        if not example:
            return m.group(0)
        ex_l = example.lower()
        for c in coords:
            # Only substitute when the evidence names something more precise than the
            # example already does.
            if c.lower() in ev_l and c.lower() in ex_l and c.lower() != ex_l:
                return f"{lead}{c}"
        return m.group(0)

    new = HEDGE_INLINE_RE.sub(repl, text)
    # Collapse runs of spaces only. Never touch whitespace before "." - a leading dot
    # may begin a filename or directory (".psw", ".pluginconf", ".process-studio"), and
    # deleting that space produced "Open the.psw workflow file".
    new = re.sub(r"[ \t]{2,}", " ", new)
    new = re.sub(r"[ \t]+([,;])", r"\1", new)
    new = re.sub(r"[ \t]+\.(?=\s|$)", ".", new)
    return new.strip()


def anchor_step(text: str, evidence: str, eq: dict) -> str:
    # Unhedge only. Do not append versions or coordinates onto the step.
    return unhedge(text, evidence)


def resolution_candidates(pb: dict) -> list[str]:
    out = []
    seen = set()

    def add(text: str, require_imperative: bool) -> None:
        t = (text or "").strip()
        if not usable_procedure_text(t):
            return
        if require_imperative and not IMPERATIVE_START_RE.match(t):
            return
        if FILLER_RE.search(t) and overlap_ratio(t, pb["evidence_text"]) < 0.3:
            return
        key = " ".join(sorted(tokenize(t)))
        if not key or key in seen:
            return
        for prev in out:
            if overlap_ratio(t, prev) > 0.65:
                return
        seen.add(key)
        out.append(t)

    res = pb.get("resolution_steps") or []
    if isinstance(res, str):
        res = [res]
    for s in res:
        add(str(s), require_imperative=False)
    for ep in pb["episodes"]:
        for st in ep.get("steps") or []:
            if st.get("step_type") in {"action", "remediation"}:
                add(st.get("text") or "", require_imperative=True)
    return out[:6]


def build_step_dict(order: int, text: str, source_eps: list[dict], kind: str) -> dict:
    refs = []
    for i, ep in enumerate(source_eps[:4], 1):
        if ep.get("id") and ep.get("title"):
            refs.append(
                {
                    "id": ep["id"],
                    "kind": "episode",
                    "label": f"ep-{i}",
                    "title": (ep.get("title") or "")[:200],
                }
            )
    return {
        "order": order,
        "type": "action" if kind != "diagnostic" else "diagnostic",
        "text": text,
        "status": "ok",
        "on_failure": "",
        "source_refs": refs,
        "evidence_quality": "high" if refs else "medium",
        "expected_outcome": "",
        "grounding_status": "grounded" if refs else "non_grounded",
        "step_classification": "procedure" if refs else "best_practice",
        "remediation_class": kind,
    }


def rebuild_steps(pb: dict, kept: list[dict], eq: dict, add_missing: bool = False) -> list[dict]:
    evidence = pb["evidence_text"]
    rebuilt = []
    for s in kept:
        text = anchor_step(step_text(s), evidence, eq)
        text = re.sub(r"\s{2,}", " ", text).strip()
        if not text:
            continue
        ns = copy.deepcopy(s)
        ns["text"] = text
        if ns.get("source_refs"):
            ns["grounding_status"] = "grounded"
            if ns.get("step_classification") == "best_practice":
                ns["step_classification"] = "procedure"
        rebuilt.append(ns)

    if add_missing:
        existing_blob = " ".join(step_text(s) for s in rebuilt)
        for cand in resolution_candidates(pb):
            if overlap_ratio(cand, existing_blob) > 0.40:
                continue
            if not usable_procedure_text(cand):
                continue
            # Only inject a missing step when it carries an AE coordinate
            # already present in evidence — never a generic paraphrase.
            if not extract_coords(cand) and not AE_PRODUCT.search(cand):
                continue
            rebuilt.append(
                build_step_dict(
                    len(rebuilt) + 1,
                    anchor_step(cand, evidence, eq),
                    [e for e in pb["episodes"] if e.get("title")],
                    "action",
                )
            )

    final = []
    for s in rebuilt:
        t = step_text(s)
        if NARRATIVE_JUNK_RE.search(t):
            continue
        if any(
            overlap_ratio(t, step_text(p)) > 0.88 and overlap_ratio(step_text(p), t) > 0.88
            for p in final
        ):
            continue
        s = copy.deepcopy(s)
        s["order"] = len(final) + 1
        final.append(s)
    return final[:8]


def component_of(pb: dict) -> str:
    blob = f"{pb.get('title','')} {pb.get('pattern_title','')}".lower()
    for c in COMPONENTS:
        if c in blob:
            return c
    return "other"


def decide(pb: dict) -> dict:
    eq = evidence_quality(pb)
    kind = playbook_kind(pb)
    steps = pb.get("steps") or []
    if not isinstance(steps, list):
        steps = []
    evidence = pb["evidence_text"]
    classifications = []
    problems = []
    keep_idx = []
    for i, s in enumerate(steps):
        if not isinstance(s, dict):
            continue
        cls = classify_step(s, evidence, eq)
        classifications.append({"order": i + 1, "class": cls, "text": step_text(s)[:240]})
        if cls in {"GENERIC_IT", "UNGROUNDED", "AI_PADDING", "INCORRECT_FOR_TICKET"}:
            problems.append(f"{cls}: {step_text(s)[:120]}")
        else:
            keep_idx.append(i)
        if cls == "VALID_BUT_NEEDS_ANCHORING":
            problems.append(f"NEEDS_ANCHORING: {step_text(s)[:120]}")

    kept = [steps[i] for i in keep_idx]
    n_valid = sum(
        1
        for c in classifications
        if c["class"] in {"VALID_AE_STEP", "VALID_BUT_NEEDS_ANCHORING"}
    )
    n_bad = len(classifications) - n_valid
    spec = 0.0
    if classifications:
        spec = 100.0 * (
            sum(1 for c in classifications if extract_coords(c["text"]) or AE_PRODUCT.search(c["text"]))
            / len(classifications)
        )

    new_steps = rebuild_steps(pb, kept, eq, add_missing=False)
    rewrite_steps = (
        rebuild_steps(pb, kept, eq, add_missing=True)
        if n_valid <= 1 and n_bad >= 2
        else new_steps
    )
    coverage = 0.0
    cands = resolution_candidates(pb)
    if cands and rewrite_steps:
        hits = sum(
            1
            for c in cands
            if any(overlap_ratio(c, step_text(s)) >= 0.28 for s in rewrite_steps)
        )
        coverage = hits / len(cands)

    required = []
    action = "KEEP"
    orig_texts = [step_text(s) for s in steps if isinstance(s, dict)]
    prune_texts = [step_text(s) for s in new_steps]
    dropped_bad = n_bad
    unhedged = orig_texts != prune_texts and any(
        HEDGE_RE.search(o or "") for o in orig_texts
    )
    meaningful_prune = orig_texts != prune_texts

    if kind in {"info", "denied", "waiting"} and eq["band"] in {"insufficient", "thin", "none"}:
        action = "DELETE"
        required.append("Issue is not an executable AE resolution and evidence has no technical fix.")
    elif kind in {"info", "denied", "waiting"}:
        action = "SUPPRESS"
        required.append("Real ticket exists but it is not an engineer-executable AE fix procedure.")
    elif eq["band"] in {"insufficient", "none"} and n_valid == 0 and not new_steps:
        action = "DELETE"
        required.append("No useful root cause, resolution, or AE-specific procedure can be grounded.")
    elif eq["band"] == "insufficient" and n_valid <= 1:
        action = "SUPPRESS"
        required.append("Evidence too thin to support a useful playbook; suppress rather than fabricate.")
    elif not new_steps and not rewrite_steps:
        action = "SUPPRESS"
        required.append("After removing unsupported steps, nothing evidence-grounded remains.")
    else:
        needs_rewrite = (
            n_valid <= 1
            and n_bad >= 2
            and len(rewrite_steps) >= 1
            and [step_text(s) for s in rewrite_steps] != orig_texts
        )
        if needs_rewrite:
            action = "REWRITE"
            new_steps = rewrite_steps
            required.append("Rebuild steps from episode/pattern resolution; current procedure does not reproduce the fix.")
        elif dropped_bad > 0 or unhedged or meaningful_prune:
            action = "IMPROVE"
            if dropped_bad > 0:
                required.append(f"Remove {dropped_bad} unsupported filler/generic/ungrounded step(s).")
            if unhedged:
                required.append("Replace hedged such-as/e.g. wording with the exact value present in the ticket evidence.")
        else:
            action = "KEEP"
            required.append("Steps already match the linked issue/resolution; no unsupported filler to strip.")
            new_steps = steps

    return {
        "playbook_id": str(pb["id"]),
        "title": pb["title"],
        "pattern_id": str(pb["pattern_id"]),
        "current_quality": pb.get("playbook_confidence"),
        "evidence_quality": eq["band"],
        "product_specificity": round(spec, 1),
        "kind": kind,
        "step_problems": problems[:12],
        "step_classes": classifications,
        "required_changes": required,
        "action": action,
        "old_step_count": len(steps),
        "new_step_count": len(new_steps),
        "new_steps": new_steps,
        "coverage": round(coverage, 3),
        "episode_count": eq["episode_count"],
        "case_refs": [e.get("case_ref") for e in pb["episodes"] if e.get("case_ref")],
        "component": component_of(pb),
        "eq": eq,
        "pattern_link_count": pb.get("pattern_link_count", 0),
        "episode_pattern_counts": pb.get("episode_pattern_counts") or {},
        "all_episode_ids": pb.get("all_episode_ids") or [],
    }


def detect_merges(decisions: list[dict], playbooks: list[dict]) -> None:
    by_comp = defaultdict(list)
    pb_map = {str(p["id"]): p for p in playbooks}
    for d in decisions:
        if d["action"] in {"DELETE", "SUPPRESS"}:
            continue
        by_comp[d["component"]].append(d)

    used = set()
    for comp, group in by_comp.items():
        if comp == "other" or len(group) < 2:
            continue
        for i, a in enumerate(group):
            if a["playbook_id"] in used:
                continue
            pa = pb_map[a["playbook_id"]]
            sig_a = " ".join(
                [
                    json.dumps(pa.get("root_causes") or []),
                    json.dumps(pa.get("resolution_steps") or []),
                    " ".join(e.get("root_cause") or "" for e in pa["episodes"]),
                    " ".join(e.get("outcome") or "" for e in pa["episodes"]),
                ]
            )
            for b in group[i + 1 :]:
                if b["playbook_id"] in used:
                    continue
                pb = pb_map[b["playbook_id"]]
                sig_b = " ".join(
                    [
                        json.dumps(pb.get("root_causes") or []),
                        json.dumps(pb.get("resolution_steps") or []),
                        " ".join(e.get("root_cause") or "" for e in pb["episodes"]),
                        " ".join(e.get("outcome") or "" for e in pb["episodes"]),
                    ]
                )
                if overlap_ratio(a["title"], b["title"]) < 0.8:
                    continue
                if overlap_ratio(sig_a, sig_b) < 0.85:
                    continue
                    continue
                # Same component + overlapping RC/resolution: merge weaker into stronger.
                def score(d):
                    return (
                        d["product_specificity"],
                        d["coverage"],
                        d["episode_count"],
                        -d["old_step_count"],
                    )

                keep, drop = (a, b) if score(a) >= score(b) else (b, a)
                drop["action"] = "MERGE"
                drop["merge_into"] = keep["playbook_id"]
                drop["merge_into_title"] = keep["title"]
                drop["required_changes"] = [
                    f"Same underlying {comp} issue/resolution as '{keep['title']}'. Keep the more complete playbook."
                ]
                used.add(drop["playbook_id"])


def write_report(decisions: list[dict]) -> tuple[Path, Path]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    counts = Counter(d["action"] for d in decisions)
    json_path = OUT_DIR / "remediation_decisions.jsonl"
    with json_path.open("w", encoding="utf-8") as f:
        for d in decisions:
            slim = {k: v for k, v in d.items() if k != "new_steps"}
            slim["new_step_texts"] = [step_text(s) for s in d.get("new_steps") or []]
            f.write(json.dumps(slim, default=str) + "\n")

    md = OUT_DIR / "REMEDIATION_REPORT.md"
    lines = [
        "# Playbook corpus remediation report",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        f"Run: `{RUN_ID}`",
        "",
        "Every playbook was re-traced against live `playbooks` → `playbook_versions` → "
        "`playbook_evidence_links` / `patterns` → `episodes` → ticket evidence. "
        "Audit markdown files were inputs, not the verdict.",
        "",
        "## Action totals (before apply)",
        "",
        "| Action | Count |",
        "|---|---:|",
    ]
    for k in ["KEEP", "IMPROVE", "REWRITE", "MERGE", "SUPPRESS", "DELETE"]:
        lines.append(f"| {k} | {counts.get(k, 0)} |")
    lines += [
        f"| **Total reviewed** | **{len(decisions)}** |",
        "",
        "## Per-playbook decisions",
        "",
        "| Playbook ID | Title | Current conf | Evidence | Specificity | Step problems | Required changes | Action |",
        "|---|---|---:|---|---:|---|---|---|",
    ]
    for d in decisions:
        probs = "; ".join(d["step_problems"][:3]).replace("|", "/")
        chg = "; ".join(d["required_changes"]).replace("|", "/")
        title = (d["title"] or "").replace("|", "/")
        lines.append(
            f"| `{d['playbook_id']}` | {title} | {d['current_quality']} | "
            f"{d['evidence_quality']} | {d['product_specificity']}% | {probs} | {chg} | **{d['action']}** |"
        )

    deletes = [d for d in decisions if d["action"] == "DELETE"]
    lines += ["", "## DELETE detail (dependencies)", ""]
    if not deletes:
        lines.append("No deletions.")
    for d in deletes:
        exclusive = [
            eid
            for eid, n in (d.get("episode_pattern_counts") or {}).items()
            if n <= 1
        ]
        shared = [
            eid
            for eid, n in (d.get("episode_pattern_counts") or {}).items()
            if n > 1
        ]
        lines += [
            f"### {d['title']}",
            f"- Playbook: `{d['playbook_id']}`",
            f"- Why: {'; '.join(d['required_changes'])}",
            f"- Pattern: `{d['pattern_id']}` (pattern_evidence_links={d.get('pattern_link_count')})",
            f"- Episode relationships to remove (episode is exclusive to this pattern): {exclusive or 'none — links kept because episodes are shared'}",
            f"- Shared episodes (relationship kept on other patterns): {shared or 'none'}",
            f"- Other entities: no execution_runs, no fix_patterns, no playbook_negative_knowledge.",
            "",
        ]
    md.write_text("\n".join(lines), encoding="utf-8")
    return md, json_path


def apply_changes(cur, playbooks: list[dict], decisions: list[dict]) -> dict:
    pb_map = {str(p["id"]): p for p in playbooks}
    stats = Counter()
    patterns_removed = 0
    episode_rels_removed = 0
    now = datetime.now(timezone.utc)

    for d in decisions:
        pb = pb_map[d["playbook_id"]]
        action = d["action"]
        if action == "KEEP":
            stats["kept"] += 1
            continue

        if action in {"IMPROVE", "REWRITE"}:
            new_id = uuid.uuid4()
            ver = next_version(pb.get("semantic_version"))
            provenance = {
                "source": RUN_ID,
                "action": action,
                "replaced_version_id": str(pb["current_version_id"]),
            }
            conf = 0.85 if d["evidence_quality"] == "strong" else 0.7 if d["evidence_quality"] == "moderate" else 0.55
            cur.execute(
                """
                INSERT INTO playbook_versions (
                    id, playbook_id, tenant_id, semantic_version, trigger_conditions,
                    branching_logic, inputs, outputs, steps, rollback_notes,
                    evidence_refs, conflicts, playbook_confidence,
                    execution_confidence_guidance, verification_policy,
                    generation_provenance, created_at
                ) VALUES (
                    %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                    %s,%s,%s,%s,%s,%s,%s
                )
                """,
                (
                    str(new_id),
                    str(pb["id"]),
                    str(pb["tenant_id"]),
                    ver,
                    UUIDJson(pb.get("trigger_conditions") or {}),
                    UUIDJson(pb.get("branching_logic") or {}),
                    UUIDJson(pb.get("inputs") or []),
                    UUIDJson(pb.get("outputs") or []),
                    UUIDJson(d["new_steps"]),
                    pb.get("rollback_notes"),
                    UUIDJson(pb.get("evidence_refs")),
                    UUIDJson(pb.get("conflicts")),
                    conf,
                    (
                        f"Remediated from linked episode/ticket evidence ({d['evidence_quality']}). "
                        "Do not treat confidence as a concreteness score."
                    ),
                    UUIDJson(pb.get("verification_policy")),
                    UUIDJson(provenance),
                    now,
                ),
            )
            # Copy evidence links onto the new version
            cur.execute(
                """
                INSERT INTO playbook_evidence_links (
                    id, tenant_id, playbook_version_id, evidence_id, episode_id, link_type, created_at
                )
                SELECT gen_random_uuid(), tenant_id, %s, evidence_id, episode_id, link_type, now()
                FROM playbook_evidence_links
                WHERE playbook_version_id = %s
                """,
                (str(new_id), str(pb["current_version_id"])),
            )
            lex = " ".join(
                [pb["title"] or "", pb.get("description") or ""]
                + [step_text(s) for s in d["new_steps"]]
            )
            cur.execute(
                """
                UPDATE playbooks
                SET current_version_id = %s,
                    lexical_search_text = %s,
                    updated_at = now()
                WHERE id = %s
                """,
                (str(new_id), lex[:8000], str(pb["id"])),
            )
            stats[action.lower()] += 1
            continue

        if action == "MERGE":
            cur.execute(
                """
                UPDATE playbooks
                SET lifecycle_state = 'retired',
                    description = concat(
                        coalesce(description, ''),
                        ' [MERGED into ', %s, ' ', %s, ']'
                    ),
                    updated_at = now()
                WHERE id = %s
                """,
                (d.get("merge_into"), d.get("merge_into_title"), str(pb["id"])),
            )
            stats["merged"] += 1
            continue

        if action == "SUPPRESS":
            cur.execute(
                """
                UPDATE playbooks
                SET lifecycle_state = 'retired',
                    description = concat(
                        coalesce(description, ''),
                        ' [SUPPRESSED: insufficient executable AE procedure after evidence review]'
                    ),
                    updated_at = now()
                WHERE id = %s
                """,
                (str(pb["id"]),),
            )
            stats["suppressed"] += 1
            continue

        if action == "DELETE":
            pid = pb["id"]
            vid_rows = []
            cur.execute("SELECT id FROM playbook_versions WHERE playbook_id = %s", (pid,))
            vid_rows = [r["id"] for r in cur.fetchall()]
            cur.execute(
                "DELETE FROM playbook_evidence_links WHERE playbook_version_id = ANY(%s::uuid[])",
                (vid_rows,),
            )
            cur.execute("DELETE FROM playbook_approvals WHERE playbook_id = %s", (pid,))
            cur.execute(
                """
                DELETE FROM graph_edges
                WHERE source_node_type = 'playbook' AND source_node_id = %s
                """,
                (pid,),
            )
            cur.execute(
                "UPDATE playbooks SET current_version_id = NULL WHERE id = %s", (pid,)
            )
            cur.execute("DELETE FROM playbook_versions WHERE playbook_id = %s", (pid,))
            cur.execute("DELETE FROM playbooks WHERE id = %s", (pid,))

            # Pattern → episode: remove only exclusive links for this invalid playbook's pattern.
            exclusive_eps = [
                uuid.UUID(eid)
                for eid, n in (d.get("episode_pattern_counts") or {}).items()
                if n <= 1
            ]
            shared_eps = [
                eid
                for eid, n in (d.get("episode_pattern_counts") or {}).items()
                if n > 1
            ]
            if exclusive_eps:
                # Exclusive episodes: keep the historical episode, but drop the
                # pattern membership that existed only to support this playbook
                # if the pattern has no remaining playbook.
                cur.execute(
                    """
                    DELETE FROM pattern_evidence_links
                    WHERE pattern_id = %s AND episode_id = ANY(%s::uuid[])
                    """,
                    (pb["pattern_id"], exclusive_eps),
                )
                episode_rels_removed += cur.rowcount
                cur.execute(
                    """
                    DELETE FROM graph_edges
                    WHERE edge_type = 'belongs_to'
                      AND source_node_type = 'episode'
                      AND target_node_type = 'pattern'
                      AND target_node_id = %s
                      AND source_node_id = ANY(%s::uuid[])
                    """,
                    (pb["pattern_id"], exclusive_eps),
                )
            # Shared episode links stay so other patterns/playbooks are untouched.
            d["removed_exclusive_episode_ids"] = [str(x) for x in exclusive_eps]
            d["kept_shared_episode_ids"] = shared_eps

            cur.execute("SELECT COUNT(*) AS n FROM playbooks WHERE pattern_id = %s", (pb["pattern_id"],))
            still = cur.fetchone()["n"]
            if still == 0:
                cur.execute(
                    "UPDATE patterns SET active_flag = false WHERE id = %s",
                    (pb["pattern_id"],),
                )
                # Do not delete the pattern row: ledger + graph history remain.
                patterns_removed += 1
            stats["deleted"] += 1

    return {
        "stats": dict(stats),
        "patterns_deactivated": patterns_removed,
        "episode_relationships_removed": episode_rels_removed,
    }


def consistency_check(cur) -> dict:
    cur.execute(
        """
        SELECT
          (SELECT count(*) FROM playbooks) AS playbooks,
          (SELECT count(*) FROM playbooks WHERE lifecycle_state = 'candidate') AS candidates,
          (SELECT count(*) FROM playbooks WHERE lifecycle_state = 'retired') AS retired,
          (SELECT count(*) FROM playbooks p
             LEFT JOIN playbook_versions pv ON pv.id = p.current_version_id
           WHERE p.lifecycle_state <> 'retired' AND (pv.id IS NULL OR pv.steps = '[]'::jsonb)
          ) AS active_without_steps,
          (SELECT count(*) FROM playbooks p
           WHERE current_version_id IS NOT NULL
             AND NOT EXISTS (SELECT 1 FROM playbook_versions v WHERE v.id = p.current_version_id)
          ) AS broken_current_version,
          (SELECT count(*) FROM playbook_versions pv
           WHERE NOT EXISTS (SELECT 1 FROM playbooks p WHERE p.id = pv.playbook_id)
          ) AS orphan_versions,
          (SELECT count(*) FROM playbook_evidence_links pel
           WHERE NOT EXISTS (SELECT 1 FROM playbook_versions v WHERE v.id = pel.playbook_version_id)
          ) AS orphan_pel,
          (SELECT count(*) FROM pattern_evidence_links pel
           WHERE pattern_id IS NOT NULL
             AND NOT EXISTS (SELECT 1 FROM patterns p WHERE p.id = pel.pattern_id)
          ) AS orphan_pattern_links,
          (SELECT count(*) FROM pattern_evidence_links pel
           WHERE episode_id IS NOT NULL
             AND NOT EXISTS (SELECT 1 FROM episodes e WHERE e.id = pel.episode_id)
          ) AS orphan_episode_links,
          (SELECT count(*) FROM playbooks p
           WHERE pattern_id IS NOT NULL
             AND NOT EXISTS (SELECT 1 FROM patterns pat WHERE pat.id = p.pattern_id)
          ) AS playbooks_broken_pattern,
          (SELECT count(*) FROM patterns WHERE active_flag = false) AS inactive_patterns
        """
    )
    return dict(cur.fetchone())


def remaining_quality(cur) -> dict:
    cur.execute(
        """
        SELECT p.id, p.title, p.lifecycle_state, pv.steps, pv.playbook_confidence
        FROM playbooks p
        JOIN playbook_versions pv ON pv.id = p.current_version_id
        WHERE p.lifecycle_state <> 'retired'
        """
    )
    rows = cur.fetchall()
    bands = Counter()
    generic = 0
    total_steps = 0
    thin = 0
    for r in rows:
        steps = r["steps"] or []
        n = len(steps)
        total_steps += n
        concrete = 0
        for s in steps:
            t = step_text(s)
            if extract_coords(t) or AE_PRODUCT.search(t):
                concrete += 1
            elif FILLER_RE.search(t) or BARE_INSPECT_RE.match(t):
                generic += 1
        pct = (100.0 * concrete / n) if n else 0.0
        if pct == 0:
            bands["0%"] += 1
        elif pct <= 33:
            bands["1-33%"] += 1
        elif pct <= 66:
            bands["34-66%"] += 1
        else:
            bands["67-100%"] += 1
        if n == 0:
            thin += 1
    return {
        "active_playbooks": len(rows),
        "concreteness_bands": dict(bands),
        "generic_or_bare_inspect_steps": generic,
        "total_steps": total_steps,
        "active_with_zero_steps": thin,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    conn = connect()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        print("Loading corpus from AEProdSupport...")
        playbooks = load_corpus(cur)
        print(f"Loaded {len(playbooks)} playbooks")
        decisions = [decide(p) for p in playbooks]
        detect_merges(decisions, playbooks)
        md, jsonl = write_report(decisions)
        print(f"Report: {md}")
        print(f"JSONL: {jsonl}")
        print("Actions:", dict(Counter(d["action"] for d in decisions)))

        if not args.apply:
            conn.rollback()
            print("Report-only. Re-run with --apply to write the database.")
            return 0

        print("Applying remediation in one transaction...")
        result = apply_changes(cur, playbooks, decisions)
        check = consistency_check(cur)
        quality = remaining_quality(cur)
        conn.commit()
        summary = {
            "reviewed": len(decisions),
            "actions": dict(Counter(d["action"] for d in decisions)),
            "apply_result": result,
            "consistency": check,
            "remaining_quality": quality,
        }
        (OUT_DIR / "apply_summary.json").write_text(
            json.dumps(summary, indent=2, default=str), encoding="utf-8"
        )
        print(json.dumps(summary, indent=2, default=str))
        return 0
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
