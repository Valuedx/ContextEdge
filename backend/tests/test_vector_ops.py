"""The halfvec cast expression must match the 0032 HNSW expression indexes."""

from sqlalchemy.dialects import postgresql

from contextedge.models.evidence import EvidenceItem
from contextedge.search.vector_ops import halfvec_cosine_distance


def test_halfvec_cosine_distance_renders_indexable_expression():
    expr = halfvec_cosine_distance(EvidenceItem.embedding, [0.1] * 4)
    sql = str(expr.compile(dialect=postgresql.dialect()))
    # The left side must be the cast the 0032 indexes are built over.
    assert "CAST(evidence_items.embedding AS HALFVEC(3072))" in sql
    # Cosine distance operator.
    assert "<=>" in sql


def test_migration_0032_declares_all_embedding_tables():
    import importlib.util
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "0032_halfvec_hnsw_indexes.py"
    )
    spec = importlib.util.spec_from_file_location("mig_0032", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    tables = {table for table, _index in module.HALFVEC_INDEXES}
    assert tables == {"evidence_items", "evidence_chunks", "decisions", "episodes"}
    assert module.down_revision == "0031_maf_context_graph_hardening"
