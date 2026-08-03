"""Closing the two known Zoho gaps.

Bare-integer ticket numbers could not be bridged from conversation, and
attachment bytes were never fetched.
"""

from __future__ import annotations

import base64
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from contextedge.connectors.zoho_desk.connector import ZohoDeskConnector, _guess_mime
from contextedge.services.ticket_bridge_service import (
    NUMERIC_CANDIDATE_CONFIDENCE,
    extract_numeric_ticket_candidates,
    extract_ticket_tokens,
)

CREDS = {
    "client_id": "c",
    "client_secret": "s",
    "refresh_token": "r",
    "org_id": "1",
    "data_center": "in",
}


def _connector(config=None):
    c = ZohoDeskConnector(config or {}, CREDS)
    c._access_token = "tok"
    c._token_expires_at = float("inf")
    return c


# --- numeric candidates ------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("please check #4021 today", ["4021"]),
        ("ticket 4021 is still open", ["4021"]),
        ("Case No. 88213 escalated", ["88213"]),
        ("incident #4021 and ticket 4099", ["4021", "4099"]),
        ("ticket number 4021", ["4021"]),
    ],
)
def test_deliberate_ticket_references_become_candidates(text, expected):
    assert extract_numeric_ticket_candidates(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "INC0010427 raised",   # already captured by the shaped pattern
        "Acme Inc. 2024 results",  # "inc" is not a cue word
    ],
)
def test_the_cue_word_never_matches_a_system_prefix_or_a_company_name(text):
    """"inc" is deliberately not a cue. Without a trailing word boundary
    it matched the prefix of "INC0010427" and produced "0010427" as a
    duplicate candidate for every ServiceNow ticket — double-counting
    each mention. Keeping it out also avoids "Acme Inc. 2024".
    """
    assert extract_numeric_ticket_candidates(text) == []


@pytest.mark.parametrize(
    "text",
    [
        "we saw 4021 timeouts",          # bare number in prose
        "rate limit is 5000 per hour",
        "version 7.4.1 build 20230321",
    ],
)
def test_ordinary_numbers_are_not_candidates(text):
    """A marker is required, so prose numbers are untouched."""
    assert extract_numeric_ticket_candidates(text) == []


def test_the_shaped_token_rule_is_unchanged():
    """The original decision stands: bare numbers are still not
    identifiers. "order #12345 is unrelated" must not become a token."""
    assert extract_ticket_tokens("order #12345 is unrelated") == []
    assert extract_ticket_tokens("INC0010427 and ITOPS-101") == [
        "INC0010427",
        "ITOPS-101",
    ]


def test_candidates_are_capped():
    text = " ".join(f"#{1000 + i}" for i in range(40))
    assert len(extract_numeric_ticket_candidates(text)) <= 10


def test_numeric_candidates_link_below_shaped_token_confidence():
    """All the evidence is in the resolution — the token shape carries
    none — so a resolved bare number must rank below a quoted
    INC0010427."""
    from contextedge.services.ticket_bridge_service import (
        BODY_CONFIDENCE,
        SUBJECT_CONFIDENCE,
    )

    assert NUMERIC_CANDIDATE_CONFIDENCE < BODY_CONFIDENCE < SUBJECT_CONFIDENCE


@pytest.mark.asyncio
async def test_unresolved_numeric_candidates_leave_no_trace():
    """Resolve-only is the whole design. An order number that matches no
    registered ticket must not become a membership OR a pending mention
    — a pending row would link retroactively the moment some unrelated
    ticket happened to carry that number.
    """
    from contextedge.services import ticket_bridge_service as mod

    evidence = SimpleNamespace(
        id=uuid.uuid4(),
        title="Invoice question",
        body_text="order #12345 is unrelated",
        thread_id=None,
    )

    class _Empty:
        def scalars(self):
            return SimpleNamespace(all=lambda: [])

        def all(self):
            return []

        def scalar_one_or_none(self):
            return None

    added: list[object] = []
    db = SimpleNamespace(
        execute=AsyncMock(return_value=_Empty()),
        flush=AsyncMock(),
        add=added.append,
    )

    with patch.object(mod, "_add_membership", AsyncMock(return_value=True)) as add_mem:
        counts = await mod.bridge_conversational_mentions(
            db, uuid.uuid4(), evidence, payload={}
        )

    assert counts.get("numeric_discarded") == 1
    assert counts["memberships"] == 0
    assert add_mem.await_count == 0
    # Nothing persisted at all — in particular no PendingIdentifierMention.
    assert [type(o).__name__ for o in added] == []


# --- attachment bytes --------------------------------------------------------


@pytest.mark.asyncio
async def test_attachments_are_not_downloaded_unless_enabled():
    """Bytes cost bandwidth on every sync and land under the tenant's
    retention policy — an operator decision, not a default."""
    connector = _connector()
    out = await connector.fetch_attachments(
        "tickets", "1", [{"id": "a1", "name": "x.pdf"}]
    )
    assert out == []


@pytest.mark.asyncio
async def test_downloaded_attachments_use_the_shape_the_registrar_consumes():
    """`attachment_refs` is metadata only and is NOT this shape: an entry
    without content is silently skipped by register_attachment_artifacts,
    which would look like attachment support while registering nothing.
    """
    connector = _connector({"download_attachments": True})

    with patch.object(
        connector, "_get_bytes", AsyncMock(return_value=b"%PDF-1.4 data")
    ):
        out = await connector.fetch_attachments(
            "tickets", "42", [{"id": "a1", "name": "runbook.pdf", "size": 100}]
        )

    assert len(out) == 1
    entry = out[0]
    assert entry["filename"] == "runbook.pdf"
    assert entry["mime_type"] == "application/pdf"
    assert base64.b64decode(entry["content_base64"]) == b"%PDF-1.4 data"


@pytest.mark.asyncio
async def test_oversized_attachments_are_skipped_before_download():
    connector = _connector({"download_attachments": True})
    with patch.object(connector, "_get_bytes", AsyncMock()) as get:
        out = await connector.fetch_attachments(
            "tickets",
            "42",
            [{"id": "a1", "name": "huge.zip", "size": 50 * 1024 * 1024}],
        )
    assert out == []
    assert get.await_count == 0


@pytest.mark.asyncio
async def test_one_failed_download_does_not_cost_the_others():
    connector = _connector({"download_attachments": True})
    calls = []

    async def flaky(path):
        calls.append(path)
        if "bad" in path:
            raise RuntimeError("boom")
        return b"ok"

    with patch.object(connector, "_get_bytes", side_effect=flaky):
        out = await connector.fetch_attachments(
            "tickets",
            "42",
            [{"id": "bad", "name": "a.txt"}, {"id": "good", "name": "b.txt"}],
        )
    assert [e["filename"] for e in out] == ["b.txt"]


def test_mime_is_guessed_because_zoho_does_not_supply_one():
    """The artifact parser dispatches on MIME; defaulting everything to
    octet-stream would send every PDF down the unsupported path."""
    assert _guess_mime("runbook.pdf") == "application/pdf"
    assert _guess_mime("SCREEN.PNG") == "image/png"
    assert _guess_mime("agent.log") == "text/plain"
    assert _guess_mime("weird.xyz") == "application/octet-stream"
    assert _guess_mime(None) == "application/octet-stream"
