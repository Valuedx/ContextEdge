"""Chunker for Jira and ServiceNow ticket evidences.

Each Jira / ServiceNow record arrives as a single ``IngestionEvent``
containing the issue summary + description (comments arrive
*separately* as their own evidence rows via the hydration worker — see
``connectors/jira_sm/connector.py:hydrate_thread`` and
``workers/hydration_tasks.py``). So at chunk-time the body is one
coherent piece of prose, not a thread of comments.

What this chunker adds over the fallback splitter:

1. **Metadata enrichment.** Ticket-specific fields the reranker and
   the future confidence model will use as features:
   ``priority``, ``status``, ``issue_type``, ``project``, ``author``
   (assignee or reporter), and the controlled-vocab
   ``source_authority`` set by the persistence layer.
2. **chunk_kind selection.** Defaults to ``"body"`` for the
   description-evidence; switches to ``"comment"`` when the payload
   carries a hydration marker (``type == "comment"`` or
   ``object_type`` ending in ``_comment``). The hydration worker
   stamps these on individual comment evidences.
3. **Title-aware composition.** Jira summary / ServiceNow
   ``short_description`` is folded into chunk 0 so a similarity hit
   on the title still surfaces the right card.

The actual splitting delegates to ``FallbackChunker`` — ticket prose
is just prose, no special structure to exploit. The boundary rules
that matter for tickets are at ingestion time (one event per issue,
one event per comment), not chunk time.
"""

from __future__ import annotations

from dataclasses import replace

from contextedge.services.chunkers.base import ChunkSpec
from contextedge.services.chunkers.fallback import FallbackChunker

# Fields the connectors stamp on the canonical payload that we want
# in chunk metadata. Mirrors what the connectors emit (see
# ``connectors/jira_sm/connector.py`` and
# ``connectors/servicenow/connector.py``).
_TICKET_METADATA_FIELDS = (
    "priority",
    "status",
    "issue_type",
    "project",
    "assignee",
    "reporter",
    "key",       # Jira issue key
    "sys_id",    # ServiceNow record id
    "category",  # ServiceNow category / Jira label-ish
)


class TicketChunker:
    """Wraps fallback splitter with Jira/ServiceNow metadata extraction."""

    name = "ticket"
    version = 1

    def __init__(self) -> None:
        self._fallback = FallbackChunker()

    def chunk(
        self,
        *,
        title: str | None,
        body: str | None,
        payload: dict,
    ) -> list[ChunkSpec]:
        chunks = self._fallback.chunk(title=title, body=body, payload=payload)
        if not chunks:
            return []

        kind = _ticket_chunk_kind(payload)
        meta_overlay = _extract_ticket_metadata(payload)

        # Merge metadata into every chunk; preserve any keys the
        # fallback chunker (or a future caller) had already set so we
        # never clobber more-specific signal with cheaper signal.
        out: list[ChunkSpec] = []
        for c in chunks:
            merged = {**meta_overlay, **c.metadata}
            out.append(replace(c, chunk_kind=kind, metadata=merged))
        return out


def _ticket_chunk_kind(payload: dict) -> str:
    """``"comment"`` for hydrated comment-evidences, ``"body"`` otherwise.

    The hydration worker for both Jira and Gmail stamps a
    ``type`` field on each per-message event (``"comment"``,
    ``"message"``, ``"description"``). We default to ``"body"`` if the
    marker is missing — the fallback for backfill or third-party
    inserts that don't go through hydration.
    """
    msg_type = (payload or {}).get("type")
    if msg_type == "comment":
        return "comment"
    return "body"


def _extract_ticket_metadata(payload: dict) -> dict:
    """Pull the ticket-relevant fields the reranker will use.

    Skips ``None`` and empty-string values so chunk metadata stays
    compact and JSONB GIN containment queries don't have to handle
    nullish values.
    """
    p = payload or {}
    out: dict[str, object] = {}
    for field in _TICKET_METADATA_FIELDS:
        value = p.get(field)
        if value not in (None, ""):
            out[field] = value
    # Author is computed: assignee wins (current owner), then reporter.
    # Stored under a single canonical key the reranker can read
    # without knowing which connector produced the chunk.
    author = p.get("assignee") or p.get("reporter") or p.get("author")
    if author:
        out["author"] = author
    return out
