"""Shared vector-distance expressions that match the ANN indexes.

pgvector's HNSW index on the ``vector`` type supports at most 2,000
dimensions; the application stores 3,072-dimensional embeddings, so plain
``embedding <=> $1`` can never be indexed (migrations 0021/0030 documented
this and dropped the invalid indexes). Migration ``0032`` instead builds
HNSW *expression* indexes over ``(embedding::halfvec(3072))``, which
supports up to 4,000 dimensions at half precision — the standard pgvector
pattern for large embeddings, at a negligible recall cost.

For the planner to use those indexes, every similarity query must order by
the *same expression*. Route all cosine-distance ordering through
``halfvec_cosine_distance`` — a raw ``column.cosine_distance(...)`` is a
guaranteed sequential scan.
"""

from __future__ import annotations

from pgvector.sqlalchemy import HALFVEC
from sqlalchemy import cast

EMBEDDING_DIMENSIONS = 3072


def halfvec_cosine_distance(column, embedding):
    """Cosine distance via ``column::halfvec(3072)``, matching the 0032
    HNSW expression indexes."""
    return cast(column, HALFVEC(EMBEDDING_DIMENSIONS)).cosine_distance(
        cast(embedding, HALFVEC(EMBEDDING_DIMENSIONS))
    )
