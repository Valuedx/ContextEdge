"""Chunker registry — mirrors ``connectors/registry.py`` shape.

``get_chunker(source_type, evidence_type)`` resolves the chunker for
a given evidence record. The mapping prefers source-type specificity
first, then falls back to ``evidence_type``, then to the generic
recursive splitter.

Concrete chunker bodies are deferred — see
``codewiki/CHUNKING_DESIGN.md`` for the per-source design table. This
module defines the resolution policy so the normalize worker can be
wired up against the protocol now and chunker implementations can
land independently.

Resolution order:

1. ``source_type in {"jira_sm", "servicenow"}`` -> ``ticket``
2. ``source_type in {"gmail", "teams"}`` -> ``thread``
3. ``evidence_type == "attachment"`` -> ``attachment``
4. otherwise -> ``fallback``
"""

from __future__ import annotations

from contextedge.services.chunkers.base import Chunker


# Populated by ``_register_chunkers`` on first ``get_chunker`` call.
chunkers: dict[str, Chunker] = {}


_TICKET_SOURCE_TYPES = frozenset({"jira_sm", "servicenow"})
_THREAD_SOURCE_TYPES = frozenset({"gmail", "teams"})


def _register_chunkers() -> None:
    """Lazy-register every concrete chunker.

    Imports are inside the function so a chunker module that fails to
    load (missing optional dep, e.g. tree-sitter for the attachment
    chunker) doesn't take down the whole evidence pipeline at import
    time. The loader logs and continues.
    """
    import structlog

    log = structlog.get_logger()

    # Each entry is (key, importer). Importer raises ImportError or
    # ModuleNotFoundError if the chunker module is missing — we treat
    # that as "not registered" rather than fatal.
    loaders: list[tuple[str, callable]] = [
        ("ticket", _load_ticket),
        ("thread", _load_thread),
        ("attachment", _load_attachment),
        ("fallback", _load_fallback),
    ]
    for key, loader in loaders:
        try:
            chunkers[key] = loader()
        except Exception as exc:  # pragma: no cover - defensive
            log.warning(
                "chunker.register_failed",
                chunker=key,
                error=str(exc),
            )


def _load_ticket() -> Chunker:
    from contextedge.services.chunkers.ticket import TicketChunker

    return TicketChunker()


def _load_thread() -> Chunker:
    from contextedge.services.chunkers.thread import ThreadChunker

    return ThreadChunker()


def _load_attachment() -> Chunker:
    from contextedge.services.chunkers.attachment import AttachmentChunker

    return AttachmentChunker()


def _load_fallback() -> Chunker:
    from contextedge.services.chunkers.fallback import FallbackChunker

    return FallbackChunker()


def get_chunker(source_type: str | None, evidence_type: str | None = None) -> Chunker:
    """Resolve the chunker for a given evidence record.

    Always returns a chunker — the ``fallback`` chunker is the floor.
    Callers do not need to handle ``None``.

    Raises ``RuntimeError`` only if the fallback chunker itself failed
    to register (a real configuration bug, not a runtime case).
    """
    if not chunkers:
        _register_chunkers()

    if source_type in _TICKET_SOURCE_TYPES and "ticket" in chunkers:
        return chunkers["ticket"]
    if source_type in _THREAD_SOURCE_TYPES and "thread" in chunkers:
        return chunkers["thread"]
    if evidence_type == "attachment" and "attachment" in chunkers:
        return chunkers["attachment"]
    if "fallback" in chunkers:
        return chunkers["fallback"]
    raise RuntimeError(
        "No chunker registered — fallback chunker failed to load. "
        "Check the structlog 'chunker.register_failed' line for the cause."
    )


def supported_chunker_names() -> list[str]:
    """Return the names of currently-registered chunkers.

    Used by the admin UI / observability to surface which chunker
    families are active in this deployment.
    """
    if not chunkers:
        _register_chunkers()
    return list(chunkers.keys())
