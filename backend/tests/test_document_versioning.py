"""Phase 4f: duplicate and version resolution for uploaded documents.

The failure this prevents is not wasted storage. It is that retrieval
silently returns superseded guidance — a playbook citing the copy in
``Old/`` because the embedding happened to favour it.
"""

from __future__ import annotations

import pytest

from contextedge.services.documents.versioning import (
    document_family,
    group_documents,
    identify,
    normalize_text,
    parse_version,
    qualifier_rank,
    text_fingerprint,
)

BODY = "The VPN certificate renewal procedure must be followed. " * 20


def _id(name, data=None, text=None, folder=""):
    return identify(
        name,
        (data or name).encode() if isinstance(data or name, str) else data,
        text=text if text is not None else BODY,
        folder=folder,
    )


# --- naming ------------------------------------------------------------------


@pytest.mark.parametrize(
    "filename,family",
    [
        ("VPN SOP.docx", "vpn sop"),
        ("VPN SOP Final.docx", "vpn sop"),
        ("VPN SOP Final v2.docx", "vpn sop"),
        ("VPN SOP Updated.pdf", "vpn sop"),
        ("Copy of VPN SOP (2).docx", "vpn sop"),
        ("VPN_SOP_rev3.docx", "vpn sop"),
    ],
)
def test_version_markers_and_revision_words_do_not_split_a_family(filename, family):
    assert document_family(filename) == family


def test_different_documents_are_different_families():
    assert document_family("Wi-Fi Troubleshooting.docx") != document_family("VPN SOP.docx")


@pytest.mark.parametrize(
    "filename,version",
    [
        ("VPN SOP v2.docx", (2,)),
        ("VPN SOP v2.1.docx", (2, 1)),
        ("VPN SOP rev 3.docx", (3,)),
        ("VPN SOP (2).docx", (2,)),
        ("VPN SOP.docx", None),
    ],
)
def test_version_numbers_are_parsed(filename, version):
    assert parse_version(filename) == version


def test_folder_counts_toward_the_revision_signal():
    """Nobody renames files when archiving them, so the folder is often
    the more reliable signal than the filename."""
    assert qualifier_rank("VPN SOP.docx", folder="Old") < 0
    assert qualifier_rank("VPN SOP.docx", folder="Archive") < 0
    assert qualifier_rank("VPN SOP Final.docx") > 0
    assert qualifier_rank("VPN SOP.docx") == 0


# --- text equality -----------------------------------------------------------

def test_a_pdf_export_of_a_word_file_is_recognised_as_the_same_text():
    """Line breaks and spacing differ; the guidance does not."""
    docx_text = "Back up the certificate.\nThen renew it.\n" * 10
    pdf_text = "Back up the certificate. Then renew  it.  " * 10
    assert text_fingerprint(docx_text) == text_fingerprint(pdf_text)


def test_short_documents_are_not_fingerprinted():
    """Two one-line files sharing a sentence are not the same document."""
    assert text_fingerprint("Restart the service.") is None
    assert text_fingerprint(None) is None


def test_normalisation_is_case_and_whitespace_insensitive():
    assert normalize_text("  Back   UP\nthe cert ") == "back up the cert"


# --- grouping ----------------------------------------------------------------


def test_byte_identical_files_group_without_review():
    data = b"identical bytes " * 40
    groups = group_documents(
        [_id("VPN SOP.docx", data), _id("VPN SOP Updated.pdf", data)]
    )
    duplicate = next(g for g in groups if g.relation == "identical_bytes")
    assert duplicate.needs_review is False
    assert duplicate.primary is not None


def test_the_realistic_upload_batch_resolves_to_one_authoritative_version():
    identities = [
        _id("VPN SOP.docx", b"a" * 400),
        _id("VPN SOP Final.docx", b"b" * 400),
        _id("VPN SOP Final v2.docx", b"c" * 400),
        _id("VPN SOP.docx", b"d" * 400, folder="Old"),
    ]
    family = next(
        g for g in group_documents(identities) if g.relation == "same_family"
    )
    assert family.primary.filename == "VPN SOP Final v2.docx"
    assert len(family.members) == 4


def test_duplicates_and_families_are_orthogonal_not_a_partition():
    """Excluding byte-duplicates from family grouping left "which SOP is
    authoritative" with two answers — one from the duplicate pair, one
    from the remaining versions, never compared."""
    same = b"z" * 400
    identities = [
        _id("VPN SOP.docx", same),
        _id("VPN SOP Updated.pdf", same),
        _id("VPN SOP Final v2.docx", b"c" * 400),
    ]
    groups = group_documents(identities)
    relations = {g.relation for g in groups}
    assert relations == {"identical_bytes", "same_family"}

    family = next(g for g in groups if g.relation == "same_family")
    # Every file participates in the supersession question.
    assert len(family.members) == 3
    assert family.primary.filename == "VPN SOP Final v2.docx"


def test_unrelated_documents_are_not_grouped():
    groups = group_documents(
        [
            _id("VPN SOP.docx", b"a" * 400),
            _id("Wi-Fi Runbook.docx", b"b" * 400, text="Different content. " * 30),
        ]
    )
    assert groups == []


def test_an_unorderable_family_declines_to_pick_a_primary():
    """Choosing arbitrarily is how the copy in Old/ becomes the one a
    playbook cites. No signal separates these, so none is chosen."""
    identities = [
        _id("VPN SOP.docx", b"a" * 400),
        _id("VPN SOP.docx", b"b" * 400),
    ]
    family = next(
        g for g in group_documents(identities) if g.relation == "same_family"
    )
    assert family.primary is None
    assert family.needs_review is True


def test_a_family_ordered_only_by_words_is_flagged_for_review():
    """Names lie. "Final" beating plain is a suggestion, not a fact."""
    identities = [
        _id("VPN SOP.docx", b"a" * 400),
        _id("VPN SOP Final.docx", b"b" * 400),
    ]
    family = next(
        g for g in group_documents(identities) if g.relation == "same_family"
    )
    assert family.primary.filename == "VPN SOP Final.docx"
    assert family.needs_review is True


def test_a_version_numbered_family_still_flags_unnumbered_rivals():
    """v2 wins over an unnumbered sibling, but the sibling might be
    newer and simply unlabelled — so a human confirms."""
    identities = [
        _id("VPN SOP v2.docx", b"a" * 400),
        _id("VPN SOP.docx", b"b" * 400),
    ]
    family = next(
        g for g in group_documents(identities) if g.relation == "same_family"
    )
    assert family.primary.filename == "VPN SOP v2.docx"
    assert family.needs_review is True


def test_tie_on_version_number_is_not_resolved():
    identities = [
        _id("VPN SOP v2.docx", b"a" * 400),
        _id("VPN SOP rev2.docx", b"b" * 400),
    ]
    family = next(
        g for g in group_documents(identities) if g.relation == "same_family"
    )
    assert family.needs_review is True


def test_empty_batch_is_handled():
    assert group_documents([]) == []
    assert group_documents([_id("only.docx", b"x" * 400)]) == []
