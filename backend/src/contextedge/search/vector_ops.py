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
from sqlalchemy import cast, text

EMBEDDING_DIMENSIONS = 3072

# The 0032 HNSW indexes are global (all tenants in one index) while every
# query post-filters by tenant_id: with the default ef_search of 40, a
# small tenant's rows can be entirely absent from the candidate set and
# the query silently returns fewer than `limit` rows. Raising ef_search
# per transaction trades a little latency for post-filter recall.
# (pgvector >= 0.8's `hnsw.iterative_scan` is the complete fix; not set
# here because SET of an unknown GUC aborts the transaction on 0.7.)
ANN_EF_SEARCH = 200


async def tune_ann_recall(db) -> None:
    """SET LOCAL hnsw.ef_search for the current transaction. Call before
    any halfvec_cosine_distance ORDER BY that post-filters by tenant."""
    await db.execute(text(f"SET LOCAL hnsw.ef_search = {ANN_EF_SEARCH}"))


def halfvec_cosine_distance(column, embedding):
    """Cosine distance via ``column::halfvec(3072)``, matching the 0032
    HNSW expression indexes."""
    return cast(column, HALFVEC(EMBEDDING_DIMENSIONS)).cosine_distance(
        cast(embedding, HALFVEC(EMBEDDING_DIMENSIONS))
    )
