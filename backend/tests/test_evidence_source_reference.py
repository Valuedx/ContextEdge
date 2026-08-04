"""Which ticket a piece of evidence actually is.

Everything needed was already stored on the raw object and none of it
reached the API, so evidence in the UI could only be identified by its
internal UUID — the one identifier nobody can search for in the source
system, quote to a colleague, or open.

Deliberately connector-agnostic: Zoho writes `ticket_number`,
ServiceNow `number`, ManageEngine `display_id`, and a reviewer just
wants the number printed on the ticket.
"""

from __future__ import annotations

import pytest

from contextedge.services.evidence_typing import source_reference_from_payload


def test_zoho_ticket_number_beats_the_internal_id():
    ref = source_reference_from_payload(
        {"ticket_number": "351086", "web_url": "https://desk.example.com/t/1"},
        "11270000091330285",
        "zoho_desk",
    )
    assert ref["display_id"] == "351086"
    assert ref["external_id"] == "11270000091330285"
    assert ref["url"] == "https://desk.example.com/t/1"


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"number": "INC0021355"}, "INC0021355"),
        ({"display_id": "4711"}, "4711"),
        ({"record_number": "RTM-9"}, "RTM-9"),
        ({"incident_number": "INC1"}, "INC1"),
        ({"key": "PROJ-42"}, "PROJ-42"),
    ],
)
def test_every_connectors_spelling_of_the_number(payload, expected):
    assert source_reference_from_payload(payload, "x", "s")["display_id"] == expected


def test_the_external_id_is_the_fallback_not_a_blank():
    """A connector with no friendlier number should still show something
    actionable rather than nothing."""
    ref = source_reference_from_payload({}, "ka::some-article", "local_file")
    assert ref["display_id"] == "ka::some-article"


def test_a_missing_payload_does_not_raise():
    for payload in (None, [], "nope", {}):
        assert source_reference_from_payload(payload, "abc", "s")["display_id"] == "abc"


def test_an_empty_number_falls_through_to_the_next_key():
    """A connector writing an empty string must not win over a real
    number further down the preference order."""
    ref = source_reference_from_payload(
        {"ticket_number": "", "number": "INC7"}, "x", "s"
    )
    assert ref["display_id"] == "INC7"


@pytest.mark.parametrize(
    "value", ["not-a-url", "javascript:alert(1)", "", None, 42, "ftp://x/y"]
)
def test_only_http_urls_are_offered_as_links(value):
    """The UI renders this as an anchor. A non-http value would become a
    link that does nothing, or worse."""
    assert source_reference_from_payload({"web_url": value}, "x", "s")["url"] is None


def test_https_and_http_are_both_accepted():
    for url in ("http://x.test/a", "https://x.test/a"):
        assert source_reference_from_payload({"url": url}, "x", "s")["url"] == url


def test_evidence_with_no_raw_object_has_no_reference():
    """Uploads have no originating record. Returning an empty shell would
    render as a blank field claiming a source that does not exist."""
    import inspect

    from contextedge.api.v1 import evidence

    source = inspect.getsource(evidence._source_reference)
    assert "if item.raw_object_ref is None:" in source
    assert "return None" in source


# --- the list, not just the detail -------------------------------------------


def test_the_list_response_carries_the_record_number():
    """Scanning a page and having to open each row to find out which
    ticket it is defeats the point of the list."""
    from contextedge.schemas.evidence import (
        EvidenceItemDetail,
        EvidenceItemResponse,
    )

    assert "source_reference" in EvidenceItemResponse.model_fields
    # Detail extends the list schema, so it inherits rather than
    # redeclaring — one definition, no drift.
    assert "source_reference" in EvidenceItemDetail.model_fields


def test_the_page_costs_one_query_not_one_per_row():
    """The list is capped at 200. An N+1 here would be 200 round trips
    to render a table column."""
    import inspect

    from contextedge.api.v1 import evidence

    source = inspect.getsource(evidence._attach_source_references)
    assert "id = any(:ids)" in source
    # No per-row fetch inside the loop.
    assert "db.get(" not in source
    assert source.count("await db.execute") == 1
    # And it attaches rather than converting: converting would validate
    # every row twice, since FastAPI validates at the boundary anyway.
    assert "model_validate" not in source


def test_whole_payloads_are_not_dragged_through_the_api():
    """Selecting raw_payload would pull every ticket body and thread
    into the API process to render a column two dozen characters wide."""
    import inspect

    from contextedge.api.v1 import evidence

    source = inspect.getsource(evidence._attach_source_references)
    # Keys are extracted in SQL; the payload column itself is never selected.
    assert "raw_payload->>" in source
    assert "select id,\n                       external_id," in source


def test_both_list_paths_are_covered():
    """Search and browse are different code paths in the same endpoint;
    covering only one leaves the column blank half the time."""
    import inspect

    from contextedge.api.v1 import evidence

    source = inspect.getsource(evidence.search_evidence)
    assert source.count("_attach_source_references") == 2
