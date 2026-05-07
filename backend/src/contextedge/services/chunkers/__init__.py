"""Source-specific chunkers for the evidence pipeline.

The public surface is :func:`get_chunker` from
``contextedge.services.chunkers.registry``. Concrete chunker
implementations live under this package, one module per family:

- ``ticket``  — Jira / ServiceNow ticket bodies + comments
- ``thread``  — Gmail / Teams thread messages
- ``attachment`` — runbooks, post-mortems, log files, code (semantic
  splitting per content kind)
- ``fallback`` — recursive splitter for unstructured prose >2 KB

See ``codewiki/CHUNKING_DESIGN.md`` for the rationale and the
per-source strategy table.
"""

from contextedge.services.chunkers.base import ChunkSpec, Chunker
from contextedge.services.chunkers.registry import (
    chunkers as chunkers,
    get_chunker as get_chunker,
)

__all__ = ["Chunker", "ChunkSpec", "get_chunker", "chunkers"]
