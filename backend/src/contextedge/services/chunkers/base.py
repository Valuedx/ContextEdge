"""Chunker protocol and ``ChunkSpec`` contract.

A *chunker* takes a normalized (post-redaction) evidence record and
emits an ordered list of :class:`ChunkSpec` — the persistence-shape
intermediate that ``services.evidence_chunk_service.write_chunks``
turns into ``EvidenceChunk`` rows.

Chunkers must be pure: no I/O, no LLM calls, no DB access. They
receive what's already on the parent ``EvidenceItem`` plus the raw
payload (already loaded by the normalize worker), and they return
specs deterministically. This keeps unit-testing trivial — feed a
fixture in, assert the spec list — and lets the same chunker run
inline (small bodies) or async via ``chunk_evidence_task``.

The chunker's ``version`` is a coarse-grained schema marker. It only
needs to bump when boundaries change in a way the existing rows are
no longer comparable to (different splitter strategy, different
metadata fields). Tuning a regex doesn't need a bump; switching from
heading-based to sentence-based does.

See ``codewiki/CHUNKING_DESIGN.md`` for the per-source strategy table.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class ChunkSpec:
    """In-memory representation of a single chunk before persistence.

    Attributes mirror columns on ``EvidenceChunk``. ``content_hash`` is
    *not* part of the spec — the persistence layer computes it so the
    hashing rule lives in one place.

    ``metadata`` keys are open-ended but the reranker, graph traversal,
    and confidence model expect (when applicable):

    - ``author``        — message / comment author identifier
    - ``ts``            — ISO timestamp of the chunk's source event
    - ``severity``      — log-line severity (``error``, ``warn``, …)
    - ``language``      — code chunk language
    - ``symbol``        — code chunk symbol name (function / class)
    - ``version``       — software / config version mentioned
    - ``env_tags``      — list of environment hints parsed from text
                          (``"ubuntu-22.04"``, ``"postgres-14"``)
    - ``source_authority`` — ``runbook`` | ``postmortem`` | ``ticket``
                          | ``chat`` | ``email`` | ``gist`` (set by the
                          chunker based on parent source / connector)

    Chunkers that don't have a value for a key just omit it — readers
    must treat missing keys as "unknown", not "false".
    """

    text: str
    chunk_kind: str
    char_offset_start: int | None = None
    char_offset_end: int | None = None
    parent_section: str | None = None
    metadata: dict = field(default_factory=dict)


@runtime_checkable
class Chunker(Protocol):
    """Pure function from (title, body, payload) -> list[ChunkSpec].

    Concrete implementations are registered in
    ``contextedge.services.chunkers.registry``. The registry maps
    ``(source_type, evidence_type)`` to a chunker instance, so the
    normalize worker doesn't need to know which chunker family handles
    a given record.
    """

    name: str
    """Stable identifier used in logs and metrics, e.g. ``"ticket"``."""

    version: int
    """Persisted on each row as ``EvidenceChunk.chunker_version``.

    Bump only when chunk boundaries or metadata schema change in a way
    that makes existing rows incomparable to new ones — that signal
    drives the re-chunk maintenance task.
    """

    def chunk(
        self,
        *,
        title: str | None,
        body: str | None,
        payload: dict,
    ) -> list[ChunkSpec]:
        """Return chunks in *source* order.

        ``title`` and ``body`` are post-redaction. ``payload`` is the
        raw connector JSON (also post-redaction at the field level via
        ``redact_evidence_fields``, but free-text fields nested inside
        the payload may still need redaction at the chunker if they're
        consumed). Implementations must not mutate ``payload``.
        """
        ...
