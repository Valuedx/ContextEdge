"""H7: the bundle, and the field a reader will skip past.

`blind_spots` is the most important thing in the response and the easiest to
ignore. It is also the field that got this wrong first: it listed only facets
that failed to answer, while the coverage facet separately reported dimensions
the deployment cannot answer at all. So a deployment with no monitoring
connector produced `blind_spots: []` — reintroducing, one layer up, exactly the
confusion the coverage work exists to remove.

The other property worth pinning is that scoping fails closed. A bundle
described as security-filtered which filters only its entry point is worse than
one that does not claim to filter.
"""

from __future__ import annotations

import uuid

from contextedge.services.diagnostic_context_service import (
    AVAILABLE,
    EMPTY,
    UNSUPPORTED,
    DiagnosticContext,
    Facet,
    _cap,
    _domain_filter,
)


def _facet(name: str, status: str) -> Facet:
    return Facet(name=name, status=status, provenance="test")


# --- blind spots -----------------------------------------------------------


def test_a_deployment_gap_is_a_blind_spot_even_when_every_facet_answered():
    """The regression. The coverage facet answering successfully — about not
    being able to answer — must not read as "nothing missing"."""
    context = DiagnosticContext(
        incident={},
        facets=[_facet("situation", AVAILABLE), _facet("coverage", AVAILABLE)],
        deployment_blind_spots=["monitoring"],
    )
    assert context.blind_spots == ["monitoring"]


def test_a_facet_that_could_not_answer_is_a_blind_spot():
    context = DiagnosticContext(
        incident={}, facets=[_facet("changes", UNSUPPORTED)]
    )
    assert context.blind_spots == ["changes"]


def test_empty_is_answerable_and_is_not_a_blind_spot():
    """`empty` means we looked and there is none — a real answer. Listing it
    would make the field noise, and a noisy warning is an ignored one."""
    context = DiagnosticContext(incident={}, facets=[_facet("recurrence", EMPTY)])
    assert context.blind_spots == []


def test_the_two_kinds_of_gap_are_merged_without_duplicates():
    context = DiagnosticContext(
        incident={},
        facets=[_facet("monitoring", UNSUPPORTED), _facet("situation", AVAILABLE)],
        deployment_blind_spots=["monitoring", "topology"],
    )
    assert context.blind_spots == ["monitoring", "topology"]


# --- scoping ---------------------------------------------------------------


def test_unrestricted_reader_gets_no_filter():
    assert _domain_filter(None) == []


def test_restricted_reader_gets_a_filter():
    assert len(_domain_filter([uuid.uuid4()])) == 1


def test_an_empty_domain_list_still_filters():
    """A reader entitled to no domains must see nothing, not everything. An
    empty list is a restriction, and treating it as absence of one is how a
    scoping bug becomes a disclosure."""
    assert len(_domain_filter([])) == 1


# --- bounding --------------------------------------------------------------


def test_a_truncated_facet_says_so():
    """Silently returning a prefix reads as a complete answer."""
    items, truncated = _cap([1, 2, 3, 4, 5], 3)
    assert items == [1, 2, 3]
    assert truncated is True


def test_an_uncapped_facet_is_not_marked_truncated():
    items, truncated = _cap([1, 2], 5)
    assert items == [1, 2]
    assert truncated is False


def test_facet_serialisation_carries_provenance_and_the_note():
    """A facet that cannot say where it came from cannot be checked."""
    facet = Facet(
        name="changes",
        status=AVAILABLE,
        provenance="situation-aware change correlation (H6)",
        items=[{"x": 1}],
        note="2 candidates, 1 confirmed",
        truncated=False,
    )
    payload = facet.as_dict()
    assert payload["provenance"].startswith("situation-aware")
    assert payload["count"] == 1
    assert payload["note"]
    assert payload["truncated"] is False
