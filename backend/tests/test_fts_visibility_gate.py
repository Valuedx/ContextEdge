"""Lexical search honours the same visibility gate as semantic search.

`search_evidence_fts` used to exclude role-blocked access policies and
nothing else, while `vector_search` excluded legal hold and pending
redaction too. A document on legal hold was therefore hidden from semantic
search and returned by lexical search — and the lexical path is the more
reachable of the two, because it also matches raw ticket payload and a
title ILIKE, so a withheld record could be pulled up by substring rather
than by embedding neighbourhood.

No live rows carried either flag when this was found (0 of 9,853), so
nothing leaked; the gap was latent and would have opened the first time
somebody put a document on hold.
"""

from __future__ import annotations

import inspect

from contextedge.search import pg_fts, vector_search


def test_fts_applies_the_shared_visibility_predicates():
    """Asserted on source rather than through a query because the gate is
    a WHERE clause on a live Postgres FTS index; what matters is that this
    surface calls the shared helper at all."""
    source = inspect.getsource(pg_fts.search_evidence_fts)

    assert "_visibility_predicates(exclude_policy_ids)" in source


def test_fts_does_not_reimplement_the_policy_check():
    """A local copy is how the two surfaces drifted apart the first time:
    the policy exclusion was duplicated here and the other two exclusions
    were simply never added."""
    source = inspect.getsource(pg_fts.search_evidence_fts)

    assert "access_policy_id.notin_" not in source


def test_the_shared_gate_still_covers_all_three_exclusions():
    """Guards the helper the fix now depends on. If someone drops a
    predicate here, both search surfaces lose it silently."""
    source = inspect.getsource(vector_search._visibility_predicates)

    assert "legal_hold" in source
    assert "pending_redaction" in source
    assert "access_policy_id" in source


def test_helper_returns_the_policy_predicate_only_when_asked():
    """Legal hold and pending redaction are unconditional; the policy
    exclusion depends on the caller's role."""
    without = vector_search._visibility_predicates(None)
    with_policy = vector_search._visibility_predicates([__import__("uuid").uuid4()])

    assert len(without) == 2
    assert len(with_policy) == 3
