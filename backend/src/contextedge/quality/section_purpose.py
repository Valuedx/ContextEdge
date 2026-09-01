"""Structure-based KB section purpose inference (Phase 2, §4.14).

Replaces ``_SECTION_PURPOSE_KEYWORDS`` in ``knowledge_retrieval_service``.
Purpose is derived from chunk structure and section headings — not from
substring matches on body text like ``required`` or ``test`` that turn
descriptive paragraphs into binding obligations.
"""

from __future__ import annotations

import re
from typing import Any

# Same command-ish detector the retrieval layer already used as a fallback,
# but applied only after structure signals — never on arbitrary prose keywords.
_COMMANDISH_RE = re.compile(
    r"(\b[a-z][\w.-]+\s+(-{1,2}[\w-]+|/[A-Za-z0-9_.-]+|[A-Za-z]:\\)|"
    r"`[^`]+`|\b[A-Z_]{3,}\b=|\.(properties|conf|xml|yaml|yml|json)\b)"
)

# Section *headings* only — the plan's complaint is body-text keyword matching.
_HEADING_ROLLBACK_RE = re.compile(
    r"\b(rollback|roll back|revert|restore|undo|backout|back out)\b",
    re.IGNORECASE,
)
_HEADING_VALIDATION_RE = re.compile(
    r"\b(validate|validation|verify|verification|health check|post-check|post check)\b",
    re.IGNORECASE,
)
_HEADING_PREREQUISITE_RE = re.compile(
    r"\b(prerequisite|pre-requisite|before you begin|requirements?|applies to)\b",
    re.IGNORECASE,
)
_HEADING_ACTION_RE = re.compile(
    r"\b(procedure|solution|resolution|steps? to reproduce|workaround|remediation|how to)\b",
    re.IGNORECASE,
)

_ACTION_CHUNK_KINDS = frozenset({"procedure_step", "code_block"})
_CONTEXT_CHUNK_KINDS = frozenset({"table", "figure", "warning"})


def infer_section_purpose(
    *,
    text: str = "",
    parent_section: str | None = None,
    chunk_kind: str = "heading_section",
) -> str:
    """Return action | prerequisite | validation | rollback | context."""
    heading = (parent_section or "").strip()
    sample = text[:1600]

    if chunk_kind in _ACTION_CHUNK_KINDS:
        return "action"

    if chunk_kind in _CONTEXT_CHUNK_KINDS:
        return "context"

    if heading:
        if _HEADING_ROLLBACK_RE.search(heading):
            return "rollback"
        if _HEADING_VALIDATION_RE.search(heading):
            return "validation"
        if _HEADING_PREREQUISITE_RE.search(heading):
            return "prerequisite"
        if _HEADING_ACTION_RE.search(heading):
            return "action"

    if chunk_kind == "procedure_step":
        return "action"

    if _COMMANDISH_RE.search(sample):
        return "action"

    return "context"


def infer_section_purpose_from_chunk(chunk: Any) -> str:
    """Convenience wrapper for evidence chunk ORM objects."""
    return infer_section_purpose(
        text=getattr(chunk, "text", "") or "",
        parent_section=getattr(chunk, "parent_section", None),
        chunk_kind=getattr(chunk, "chunk_kind", "heading_section"),
    )
