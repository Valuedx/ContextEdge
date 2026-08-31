"""Knowledge applicability is extracted on the INGEST path, not only on
a manual re-classification.

`_extract_applicability` was called from `_classify` (the manual
`extraction.classify_relevance` task) and from nowhere else, so an article
that arrived through a normal sync never got one. Measured on the live
corpus: 7 of 133 articles carried applicability, and those 7 had been
re-classified by hand. The feature that makes knowledge retrieval version-
and environment-aware was dead for ingested content, and silently, because
a missing applicability degrades to lexical matching rather than erroring.
"""

from __future__ import annotations

import inspect

from contextedge.workers import extraction_tasks


def test_normalize_extracts_applicability():
    """The regression this module exists for. Asserted on source because
    _normalize is a long DB- and network-bound ingest path; what matters
    is that the call is present on it at all."""
    source = inspect.getsource(extraction_tasks._normalize)

    assert "_extract_applicability(db, ev, tenant_id, payload)" in source


def test_manual_reclassification_still_extracts_applicability():
    """The path that used to be the only one keeps working — re-running
    classification by hand is how a stale article gets refreshed."""
    source = inspect.getsource(extraction_tasks._classify)

    assert "_extract_applicability(db, ev, tenant_id, payload)" in source
    assert "load_raw_payload" in source


def test_extractor_skips_non_knowledge_and_already_populated():
    """Cheapness is what makes calling it on every ingest acceptable: a
    ticket returns immediately, and an article that already has an
    applicability is never re-billed."""
    source = inspect.getsource(extraction_tasks._extract_applicability)

    assert "not in KNOWLEDGE_EVIDENCE_TYPES" in source
    assert "if not ev.applicability:" in source
    assert "return" in source


def test_stated_facets_short_circuit_the_model_call():
    """What the source states beats what a model infers from the same
    text, and costs nothing. Guards the ordering: if the LLM call moved
    above this, every ingest would pay ~7,200 tokens it does not need."""
    source = inspect.getsource(extraction_tasks._extract_applicability)

    stated_at = source.index("applicability_from_facets")
    llm_at = source.index("extract_applicability_llm(")

    assert stated_at < llm_at


def test_official_catalog_does_not_call_the_article_extractor():
    """A version-list page is not an article. Extracting it would stamp
    one release and mismatch every ticket that is not on that line."""
    source = inspect.getsource(extraction_tasks._extract_applicability)
    assert "official_catalog" in source
    catalog_at = source.index("official_catalog")
    llm_at = source.index("extract_applicability_llm(")
    assert catalog_at < llm_at
