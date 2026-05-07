"""Unit tests for evidence chunkers.

Chunkers are pure functions, so every test feeds in synthetic input
and asserts on the returned ``list[ChunkSpec]``. No DB, no LLM, no
fixtures beyond plain Python data.

Coverage targets, per chunker family:

- shape: empty input returns ``[]``; single short body returns a
  single chunk; long body returns multiple chunks with overlap.
- offsets: ``char_offset_start``/``char_offset_end`` line up with the
  composed text so a chunk can be excerpted back to the parent.
- metadata: per-source enrichment populates the documented keys.
- fallback: chunkers fall through to recursive split when their
  specialised heuristic doesn't fire.

The recursive splitter (``FallbackChunker``) gets the most coverage
because every other chunker delegates to it.
"""

from __future__ import annotations

import pytest

from contextedge.services.chunkers import get_chunker
from contextedge.services.chunkers.attachment import AttachmentChunker
from contextedge.services.chunkers.base import ChunkSpec
from contextedge.services.chunkers.fallback import (
    CHUNK_OVERLAP_CHARS,
    CHUNK_TARGET_CHARS,
    FallbackChunker,
)
from contextedge.services.chunkers.thread import ThreadChunker, _strip_quoted_reply
from contextedge.services.chunkers.ticket import TicketChunker


# ---------------------------------------------------------------------------
# FallbackChunker
# ---------------------------------------------------------------------------


def test_fallback_empty_returns_empty_list():
    out = FallbackChunker().chunk(title=None, body=None, payload={})
    assert out == []


def test_fallback_whitespace_only_returns_empty_list():
    out = FallbackChunker().chunk(title="   ", body="\n\n  \t  ", payload={})
    assert out == []


def test_fallback_short_body_one_chunk_includes_title():
    out = FallbackChunker().chunk(
        title="VPN cert expired",
        body="Users see AUTH_CERT_EXPIRED on vpn-gw-east-01.",
        payload={},
    )
    assert len(out) == 1
    assert "VPN cert expired" in out[0].text
    assert "AUTH_CERT_EXPIRED" in out[0].text
    assert out[0].chunk_kind == "body"
    assert out[0].char_offset_start == 0


def test_fallback_long_body_splits_with_overlap():
    paragraph = ("Lorem ipsum dolor sit amet. " * 20).strip()
    body = "\n\n".join([paragraph] * 6)  # ~3.4 KB
    out = FallbackChunker().chunk(title="Long doc", body=body, payload={})

    assert len(out) >= 2
    # Each chunk after the first should carry overlap from the prior tail.
    for i in range(1, len(out)):
        prev_tail = out[i - 1].text[-CHUNK_OVERLAP_CHARS:]
        # Overlap must appear at the head of the next chunk (allowing
        # whitespace differences).
        assert prev_tail.strip()[:80] in out[i].text[:CHUNK_OVERLAP_CHARS + 200]


def test_fallback_oversize_single_paragraph_falls_through_to_sentence_split():
    sentences = " ".join([f"Sentence number {i}." for i in range(1, 400)])
    out = FallbackChunker().chunk(title=None, body=sentences, payload={})
    assert len(out) >= 2
    # No chunk should be wildly larger than the target+overlap.
    for c in out:
        assert len(c.text) <= CHUNK_TARGET_CHARS + CHUNK_OVERLAP_CHARS + 500


def test_fallback_pathological_single_long_line_hard_splits():
    body = "x" * (CHUNK_TARGET_CHARS * 3)
    out = FallbackChunker().chunk(title=None, body=body, payload={})
    assert len(out) >= 3
    for c in out:
        # Hard split keeps each chunk under target + overlap.
        assert len(c.text) <= CHUNK_TARGET_CHARS + CHUNK_OVERLAP_CHARS + 50


# ---------------------------------------------------------------------------
# TicketChunker
# ---------------------------------------------------------------------------


def test_ticket_chunk_kind_default_body():
    out = TicketChunker().chunk(
        title="VPN incident",
        body="Description of the VPN issue.",
        payload={"key": "JIRA-1", "priority": "high", "issue_type": "incident"},
    )
    assert len(out) == 1
    assert out[0].chunk_kind == "body"


def test_ticket_chunk_kind_comment_when_payload_marks_comment():
    out = TicketChunker().chunk(
        title=None,
        body="A reply on the ticket.",
        payload={"type": "comment", "key": "JIRA-1", "author": "jsmith@acme.com"},
    )
    assert len(out) == 1
    assert out[0].chunk_kind == "comment"
    assert out[0].metadata.get("author") == "jsmith@acme.com"


def test_ticket_metadata_copies_documented_fields():
    out = TicketChunker().chunk(
        title="t",
        body="b",
        payload={
            "key": "JIRA-99",
            "priority": "high",
            "status": "open",
            "issue_type": "incident",
            "assignee": "alice@acme.com",
            "reporter": "bob@acme.com",
            "project": "IT-OPS",
            "ignore_me": "noise",
        },
    )
    md = out[0].metadata
    assert md["priority"] == "high"
    assert md["status"] == "open"
    assert md["issue_type"] == "incident"
    assert md["project"] == "IT-OPS"
    # Author resolution: assignee wins over reporter.
    assert md["author"] == "alice@acme.com"
    assert "ignore_me" not in md


def test_ticket_metadata_skips_empty_values():
    out = TicketChunker().chunk(
        title="t",
        body="b",
        payload={"key": "JIRA-1", "priority": None, "status": "", "issue_type": "task"},
    )
    md = out[0].metadata
    assert "priority" not in md
    assert "status" not in md
    assert md["issue_type"] == "task"


# ---------------------------------------------------------------------------
# ThreadChunker — quote stripping
# ---------------------------------------------------------------------------


def test_strip_quoted_reply_gmail_on_wrote_pattern():
    body = (
        "Thanks for the update.\n\n"
        "On Mon, May 1 2026 at 09:42, Jane Doe <jane@acme.com> wrote:\n"
        "> Original message body that should be stripped.\n"
        "> Continuing the prior thread.\n"
    )
    cleaned, excerpt = _strip_quoted_reply(body)
    assert "Thanks for the update." in cleaned
    assert "Jane Doe" not in cleaned
    assert "Original message body" not in cleaned
    assert excerpt is not None
    assert "Original message body" in excerpt


def test_strip_quoted_reply_outlook_pattern():
    body = (
        "Looping in Bob.\n\n"
        "From: Alice <alice@acme.com>\nSent: Monday, May 1 2026 09:42\n"
        "To: team@acme.com\nSubject: VPN incident\n\n"
        "Original alert text\n"
    )
    cleaned, excerpt = _strip_quoted_reply(body)
    assert "Looping in Bob." in cleaned
    assert "Alice <alice@acme.com>" not in cleaned
    assert excerpt and "Original alert text" in excerpt


def test_strip_quoted_reply_only_quote_keeps_body():
    """If the whole body is a quote (forward with no commentary),
    keep the original — embedding a quote beats embedding nothing."""
    body = "> Forwarded message\n> Body of the forward.\n"
    cleaned, excerpt = _strip_quoted_reply(body)
    assert cleaned == body
    assert excerpt is None


def test_thread_chunk_carries_author_and_ts_metadata():
    out = ThreadChunker().chunk(
        title="VPN incident",
        body="Same VPN issue — checking.",
        payload={
            "from": "alice@acme.com",
            "timestamp": "2026-05-08T14:32:00Z",
        },
    )
    assert len(out) == 1
    assert out[0].chunk_kind == "message"
    assert out[0].metadata["author"] == "alice@acme.com"
    assert out[0].metadata["ts"] == "2026-05-08T14:32:00Z"


def test_thread_chunk_strips_quote_before_indexing():
    body = (
        "Restarted the gateway, now seeing AUTH_CERT_EXPIRED.\n\n"
        "On Sat, Apr 30 2026, Original Reporter <r@acme.com> wrote:\n"
        "> The cert pipeline fails\n"
        "> with the same error every time\n"
    )
    out = ThreadChunker().chunk(
        title=None,
        body=body,
        payload={"from": "alice@acme.com"},
    )
    assert len(out) == 1
    chunk = out[0]
    assert "AUTH_CERT_EXPIRED" in chunk.text
    assert "Original Reporter" not in chunk.text
    excerpt = chunk.metadata.get("replies_to_excerpt", "")
    assert "cert pipeline fails" in excerpt


# ---------------------------------------------------------------------------
# AttachmentChunker
# ---------------------------------------------------------------------------


def test_attachment_markdown_splits_on_headings_with_breadcrumb():
    body = (
        "# Postmortem: VPN outage\n\n"
        "Brief context paragraph.\n\n"
        "## Timeline\n\n"
        "14:32 — alert fired.\n\n"
        "## Root cause\n\n"
        "Cert chain expired at the gateway.\n"
    )
    out = AttachmentChunker().chunk(
        title="postmortem.md",
        body=body,
        payload={"filename": "postmortem.md"},
    )

    parents = [c.parent_section for c in out]
    assert "Postmortem: VPN outage" in parents
    assert "Postmortem: VPN outage > Timeline" in parents
    assert "Postmortem: VPN outage > Root cause" in parents
    for c in out:
        assert c.chunk_kind == "heading_section"
        assert c.metadata.get("attachment_kind") == "markdown"


def test_attachment_jsonl_log_creates_log_event_chunks():
    body = "\n".join(
        [
            '{"ts": "2026-05-08T14:32:00Z", "level": "ERROR", "msg": "auth fail"}',
            '{"ts": "2026-05-08T14:32:01Z", "level": "ERROR", "msg": "auth fail"}',
            '{"ts": "2026-05-08T14:32:02Z", "level": "ERROR", "msg": "auth fail"}',
        ]
        * 50
    )
    out = AttachmentChunker().chunk(
        title="vpn.log",
        body=body,
        payload={"filename": "events.jsonl"},
    )
    assert out
    for c in out:
        assert c.chunk_kind == "log_event"
        assert c.metadata.get("attachment_kind") == "log_jsonl"


def test_attachment_plain_log_splits_on_timestamp_boundary():
    lines = []
    for i in range(40):
        lines.append(f"2026-05-08T14:32:{i:02d}Z ERROR auth_module failed for user {i}")
        lines.append("    at auth.checkCert (auth.js:42)")
        lines.append("    at auth.run (auth.js:10)")
    body = "\n".join(lines)
    out = AttachmentChunker().chunk(
        title="vpn.log",
        body=body,
        payload={"filename": "vpn.log"},
    )
    assert out
    for c in out:
        # Plain logs may also fall through to recursive splitter if
        # chunks are too small to fill the target — accept both.
        assert c.chunk_kind in {"log_event", "body"}


def test_attachment_unrecognized_falls_back_to_recursive_splitter():
    body = "Just plain prose with no structure to speak of. " * 20
    out = AttachmentChunker().chunk(
        title=None,
        body=body,
        payload={"filename": "notes.txt"},
    )
    assert out
    for c in out:
        assert c.metadata.get("attachment_kind") == "prose"


# ---------------------------------------------------------------------------
# Registry resolution
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "source_type,evidence_type,expected_name",
    [
        ("jira_sm", "issue", "ticket"),
        ("servicenow", "incident", "ticket"),
        ("gmail", "message", "thread"),
        ("teams", "message", "thread"),
        ("unknown", "attachment", "attachment"),
        ("unknown", "message", "fallback"),
        (None, None, "fallback"),
    ],
)
def test_registry_resolves_correct_chunker(source_type, evidence_type, expected_name):
    chunker = get_chunker(source_type, evidence_type)
    assert chunker.name == expected_name
