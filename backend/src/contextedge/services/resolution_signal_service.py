"""Does a cluster contain a solution in some form? (resolution gate)

ContextEdge's product is reusable resolution knowledge, and episode
synthesis is the costliest lane in the pipeline — so a cluster with no
resolution signal ANYWHERE defers synthesis instead of paying for it
("deferred, not dropped": when a resolution-bearing item later joins
the cluster, the next reconstruct dispatch passes the gate and the
episode is built).

Detection is deterministic — no LLM call — via a signal hierarchy:

1. Structural: closed/resolved status vocabulary and close-notes
   phrasing that connectors put into evidence text.
2. Lexical: resolution vocabulary ("resolved by", "root cause",
   "workaround", "fixed after ...") tuned for precision — a gate that
   passes on noise spends the money it exists to save.
3. Already-paid classifier output: the v2 relevance summaries state
   action/outcome ("resolved by re-uploading web drivers via
   SysAdmin"), so scanning body_summary inherits LLM judgment at zero
   marginal cost.

The gate is a CLUSTER property, never an evidence filter: in
scattered-source deployments the problem arrives from one system and
the fix from another, and dropping problem-side evidence would destroy
the identifiers correlation needs to join them later.
"""

from __future__ import annotations

import re
import uuid

import structlog

logger = structlog.get_logger()

# Precision-first: each pattern should be hard to emit without an
# actual resolution being described. Conversational "we will fix"
# (future tense, no outcome) deliberately does not match.
_RESOLUTION_RE = re.compile(
    r"(?i)\b(?:"
    # Bare "resolved" matches problem language ("needs to be resolved
    # urgently") — the qualifier is required; statusy forms have their
    # own alternations below.
    r"resolved\s+(?:by|via|after|through|with)|"
    r"fixed(?:\s+(?:by|via|after|through|with))|"
    r"issue\s+(?:was|is|has\s+been)\s+(?:fixed|resolved|closed)|"
    r"root\s+cause(?:\s+(?:was|is|identified|found))?|"
    r"workaround(?:\s+(?:is|was|applied|provided))?|"
    r"solution(?:\s+(?:is|was|provided|applied|implemented))|"
    r"closing\s+(?:this\s+)?(?:the\s+)?ticket|"
    r"status\s*[:=]?\s*(?:closed|resolved)|"
    r"resolved\s+by\s+agent|"
    r"working\s+(?:fine|as\s+expected|now)\s+after"
    r")\b"
)

# How much of each evidence item's text the scan reads: the HEAD and
# the TAIL, because resolution language concentrates in summaries,
# close notes, and thread tails. Head-only scanning was Lesson 3
# (LESSONS_LEARNED: "the fix is at the bottom of the thread") repeated
# — a resolution past the head slice deferred a resolvable cluster.
SCAN_CHARS = 4_000
MAX_ITEMS_SCANNED = 200


def text_has_resolution_signal(text: str | None) -> bool:
    if not text:
        return False
    if _RESOLUTION_RE.search(text[:SCAN_CHARS]) is not None:
        return True
    return (
        len(text) > SCAN_CHARS
        and _RESOLUTION_RE.search(text[-SCAN_CHARS:]) is not None
    )


async def cluster_has_resolution_signal(
    db,
    tenant_id: uuid.UUID,
    evidence_ids: list[uuid.UUID],
) -> bool:
    """True when ANY evidence in the cluster carries a resolution
    signal — in its summary (the strongest, LLM-distilled source), its
    title, or its body text. Scanned newest-first: the resolution is
    the END of an incident's timeline, so if the item cap ever binds,
    the items it drops are the ones least likely to carry the signal."""
    from sqlalchemy import func, select

    from contextedge.models.evidence import EvidenceItem

    if not evidence_ids:
        return False
    rows = (
        await db.execute(
            select(
                EvidenceItem.title,
                EvidenceItem.body_summary,
                EvidenceItem.body_text,
            )
            .where(
                EvidenceItem.tenant_id == tenant_id,
                EvidenceItem.id.in_(evidence_ids),
            )
            .order_by(
                func.coalesce(
                    EvidenceItem.evidence_time, EvidenceItem.ingested_at
                ).desc()
            )
            .limit(MAX_ITEMS_SCANNED)
        )
    ).all()
    for title, summary, body in rows:
        if (
            text_has_resolution_signal(summary)
            or text_has_resolution_signal(title)
            or text_has_resolution_signal(body)
        ):
            return True
    return False
