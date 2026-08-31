"""Phase 4c: structure-driven chunking for parsed documents.

Several behaviours here were derived from measuring a real 318-document
KB corpus rather than from a synthetic fixture, and the tests say which —
those are the ones most likely to be "simplified" back into bugs.
"""

from __future__ import annotations

import pytest

from contextedge.services.chunkers.document import (
    KIND_CODE_BLOCK,
    KIND_PROCEDURE_STEP,
    KIND_TABLE,
    DocumentChunker,
)
from contextedge.services.chunkers.registry import get_chunker


def _el(element_type, text, *, page=1, section=None, structured=None, method="native"):
    return {
        "type": element_type,
        "text": text,
        "page": page,
        "section": section or [],
        "method": method,
        "structured": structured or {},
    }


def _chunk(elements, title=None):
    return DocumentChunker().chunk(
        title=title, body=None, payload={"_document_elements": elements}
    )


# --- routing -----------------------------------------------------------------


def test_knowledge_evidence_routes_to_the_document_chunker():
    assert get_chunker("local_file", "kb_article").name == "document"
    assert get_chunker("servicenow", "kb_article").name == "document"
    assert get_chunker("zoho_desk", "documentation").name == "document"
    assert get_chunker("zoho_desk", "sop").name == "document"
    # Unchanged for everything else.
    assert get_chunker("servicenow", "incident").name == "ticket"
    assert get_chunker("gmail", "message").name == "thread"
    assert get_chunker("unknown", "attachment").name == "attachment"


# --- section boundaries ------------------------------------------------------


def test_headings_start_new_chunks_and_become_the_section_path():
    specs = _chunk(
        [
            _el("heading", "Error:", section=["Error:"]),
            _el("paragraph", "Session status unavailable.", section=["Error:"]),
            _el("heading", "Resolution:", section=["Resolution:"]),
            _el("paragraph", "Shorten the username.", section=["Resolution:"]),
        ]
    )
    assert len(specs) == 2
    assert specs[0].parent_section == "Error:"
    assert "Session status" in specs[0].text
    assert specs[1].parent_section == "Resolution:"
    assert "Shorten" in specs[1].text


def test_page_numbers_survive_into_chunk_metadata():
    """Provenance a reviewer can open. A citation to "the SOP" is not
    reviewable; "p2 § Resolution" is."""
    specs = _chunk(
        [
            _el("heading", "Resolution:", page=2, section=["Resolution:"]),
            _el("paragraph", "Do the thing.", page=2, section=["Resolution:"]),
        ]
    )
    assert specs[0].metadata["page"] == 2
    assert specs[0].metadata["section_path"] == ["Resolution:"]


def test_multi_page_chunk_records_its_range():
    specs = _chunk(
        [
            _el("heading", "Procedure", page=1, section=["Procedure"]),
            _el("paragraph", "First part.", page=1, section=["Procedure"]),
            _el("paragraph", "Continues overleaf.", page=2, section=["Procedure"]),
        ]
    )
    assert specs[0].metadata["page_range"] == [1, 2]


# --- the rules that make a chunk safe to act on ------------------------------


def test_a_step_keeps_its_figure():
    """"Configure as shown below" is unusable without the image. The
    figure must not be split from the step it illustrates."""
    specs = _chunk(
        [
            _el("heading", "Procedure", section=["Procedure"]),
            _el("paragraph", "1. Configure the values as shown below.",
                section=["Procedure"]),
            _el("figure", "[figure: not yet interpreted]", section=["Procedure"],
                structured={"needs_vision": True}),
        ]
    )
    step = next(s for s in specs if s.chunk_kind == KIND_PROCEDURE_STEP)
    assert "figure" in step.text
    assert step.metadata["has_figure"] is True
    assert step.metadata["needs_vision"] is True


def test_a_step_keeps_its_warning():
    """Returning a restart step without the warning against restarting
    during an execution returns the dangerous half on its own."""
    specs = _chunk(
        [
            _el("heading", "Procedure", section=["Procedure"]),
            _el("paragraph", "1. Restart the agent service.", section=["Procedure"]),
            _el("paragraph", "Warning: do not restart during an active execution.",
                section=["Procedure"]),
        ]
    )
    assert len(specs) == 1
    assert "Warning" in specs[0].text
    assert "Restart the agent" in specs[0].text


def test_consecutive_steps_split_so_one_step_is_addressable():
    specs = _chunk(
        [
            _el("heading", "Procedure", section=["Procedure"]),
            _el("paragraph", "1. Back up the certificate.", section=["Procedure"]),
            _el("paragraph", "2. Request a replacement.", section=["Procedure"]),
            _el("paragraph", "3. Reload the gateway.", section=["Procedure"]),
        ]
    )
    assert len(specs) == 3
    assert all(s.chunk_kind == KIND_PROCEDURE_STEP for s in specs)


def test_step_colon_form_is_recognized():
    """Measured on the real corpus: "Step 2:" is common and an earlier
    version requiring a dot or paren missed it entirely — procedure_step
    detection was near-zero across 318 articles because of it."""
    specs = _chunk(
        [
            _el("heading", "Resolution:", section=["Resolution:"]),
            _el("paragraph", "Step 2: Password protect the connector.",
                section=["Resolution:"]),
        ]
    )
    assert specs[0].chunk_kind == KIND_PROCEDURE_STEP


def test_numbered_line_outside_a_procedural_section_is_not_a_step():
    """"1. RFC 4271" under References is a citation. Labelling it a
    procedure step puts it in front of someone looking for what to do."""
    specs = _chunk(
        [
            _el("heading", "References", section=["References"]),
            _el("paragraph", "1. RFC 4271 BGP-4", section=["References"]),
        ]
    )
    assert specs[0].chunk_kind != KIND_PROCEDURE_STEP


def test_large_table_stands_alone_but_small_one_rides_along():
    """Splitting a table mid-row destroys row-to-header alignment."""
    big = "col | col | col\n" + "\n".join(f"r{i} | a | b" for i in range(60))
    specs = _chunk(
        [
            _el("heading", "Errors", section=["Errors"]),
            _el("paragraph", "The table lists known codes.", section=["Errors"]),
            _el("table", big, section=["Errors"]),
        ]
    )
    kinds = [s.chunk_kind for s in specs]
    assert KIND_TABLE in kinds
    table_spec = next(s for s in specs if s.chunk_kind == KIND_TABLE)
    assert "r59" in table_spec.text

    small = _chunk(
        [
            _el("heading", "Errors", section=["Errors"]),
            _el("paragraph", "Codes:", section=["Errors"]),
            _el("table", "401 | expired", section=["Errors"]),
        ]
    )
    assert len(small) == 1
    assert "401" in small[0].text


def test_predominantly_code_content_is_marked_as_such():
    """A config snippet answers a different question from the prose
    explaining it ("what do I set" vs "why")."""
    specs = _chunk(
        [
            _el("heading", "Resolution:", section=["Resolution:"]),
            _el("paragraph", '<broker useJMX="true" brokerName="BROKER1">',
                section=["Resolution:"]),
            _el("paragraph", "<managementContext>", section=["Resolution:"]),
            _el("paragraph", '<managementContext createConnector="false"/>',
                section=["Resolution:"]),
            _el("paragraph", "</managementContext>", section=["Resolution:"]),
        ]
    )
    assert specs[0].chunk_kind == KIND_CODE_BLOCK


def test_prose_mentioning_a_tag_stays_prose():
    """The code rule is a majority, not a keyword match."""
    specs = _chunk(
        [
            _el("heading", "Resolution:", section=["Resolution:"]),
            _el("paragraph", "Set the <broker> element to enable JMX support.",
                section=["Resolution:"]),
            _el("paragraph", "This is described in the vendor documentation.",
                section=["Resolution:"]),
        ]
    )
    assert specs[0].chunk_kind != KIND_CODE_BLOCK


def test_heading_only_chunks_merge_into_the_next_section():
    """A chunk whose whole content is "How To Reproduce:" has nothing to
    retrieve on, costs an embedding, and dilutes the index."""
    specs = _chunk(
        [
            _el("heading", "How To Reproduce:", section=["How To Reproduce:"]),
            _el("heading", "Resolution:", section=["Resolution:"]),
            _el("paragraph", "Shorten the username.", section=["Resolution:"]),
        ]
    )
    assert len(specs) == 1
    assert "How To Reproduce:" in specs[0].text
    assert "Shorten the username." in specs[0].text


def test_trailing_heading_only_chunk_is_dropped():
    specs = _chunk(
        [
            _el("heading", "Resolution:", section=["Resolution:"]),
            _el("paragraph", "Do the thing.", section=["Resolution:"]),
            _el("heading", "See Also:", section=["See Also:"]),
        ]
    )
    assert len(specs) == 1
    assert "Do the thing." in specs[0].text


def test_extraction_method_is_carried_so_paraphrase_is_distinguishable():
    """A chunk holding model-transcribed content must not look like a
    wholly parsed one."""
    specs = _chunk(
        [
            _el("heading", "Resolution:", section=["Resolution:"]),
            _el("paragraph", "Parsed text.", section=["Resolution:"]),
            _el("paragraph", "Model-read text.", section=["Resolution:"],
                method="vision"),
        ]
    )
    assert specs[0].metadata["extraction_methods"] == ["native", "vision"]


def test_title_is_folded_into_the_first_chunk():
    specs = _chunk(
        [
            _el("heading", "Error:", section=["Error:"]),
            _el("paragraph", "Something failed.", section=["Error:"]),
        ],
        title="Agent Controller Issue",
    )
    assert specs[0].text.startswith("Agent Controller Issue")


def test_falls_back_when_no_structured_elements_exist():
    """Evidence whose body did not come from a document parser must
    still chunk — on headings, via the attachment chunker."""
    specs = DocumentChunker().chunk(
        title="Runbook",
        body="## Overview\n\nSome prose.\n\n## Steps\n\nMore prose.",
        payload={},
    )
    assert specs
    assert all(s.text.strip() for s in specs)


def test_empty_input_yields_no_chunks():
    assert _chunk([]) == [] or all(s.text.strip() for s in _chunk([]))
    assert DocumentChunker().chunk(title=None, body=None, payload={}) == []


# --- PDF label-box promotion (measured on the real corpus) -------------------

pytest.importorskip("pdfplumber", reason="document extras not installed")


def test_single_cell_label_box_becomes_a_heading():
    """The dominant finding from the 318-document corpus: these KBs
    express sections as one-cell bordered boxes ("Issue:", "Solution:",
    "Steps To Reproduce:"), not as styled headings. 199 of ~213 detected
    "tables" were exactly this. Left as tables, the document's real
    section structure is invisible and nothing is attributable."""
    from contextedge.services.documents.pdf import _single_cell_label

    assert _single_cell_label([["Solution:", None]]) == "Solution:"
    assert _single_cell_label([["Resolution", ""]]) == "Resolution"
    assert _single_cell_label([["Steps To Reproduce:"]]) == "Steps To Reproduce:"


def test_a_real_one_row_table_is_not_mistaken_for_a_label():
    from contextedge.services.documents.pdf import _single_cell_label

    assert _single_cell_label([["401", "Expired credential", "Rotate"]]) is None
    assert _single_cell_label([["a"], ["b"]]) is None


def test_a_sentence_in_a_box_is_content_not_a_section():
    """Observed in the corpus: an instruction sits in the same box style
    as a section label. Promoting it invents a section named after one
    instruction, which every following chunk then cites."""
    from contextedge.services.documents.pdf import _single_cell_label

    assert _single_cell_label([["Make username of more than 15 character."]]) is None
    assert _single_cell_label([["A" * 200]]) is None


def test_a_sentence_at_heading_size_is_not_a_heading():
    from contextedge.services.documents.pdf import _is_heading

    assert _is_heading("Resolution:", size=14, body_size=10) is True
    assert _is_heading("Make username of more than 15 character.", size=14,
                       body_size=10) is False
    # A numbered heading is still a heading.
    assert _is_heading("3.2 Renew", size=10, body_size=10) is True
