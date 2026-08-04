"""Stripping quoted history out of thread messages.

Measured on 304 real Zoho messages across 19 threads: 3,051,681
characters of raw body reduced to 230,811 — **92%** of what would
otherwise be embedded, chunked and read by every extractor was the same
conversation quoted again. The worst single thread held 323,921
characters of which 20,970 were new.

That is not merely wasteful: it fills the graph with near-duplicate
chunks so retrieval returns the same paragraph repeatedly, and makes
identity extraction re-read the same names, skewing the mention
frequencies candidate generation and reconciliation depend on.
"""

from __future__ import annotations

import pytest

from contextedge.services.thread_text_service import (
    clean_thread_bodies,
    dedupe_against,
    strip_quoted,
)


# --- structural stripping -----------------------------------------------------


def test_a_reply_keeps_its_own_words_and_loses_the_quote():
    body = (
        "Hi team, restarting the broker fixed it.\n"
        "\n"
        "On 3 August 2026 at 14:02, Priyanka Patil <p@x.com> wrote:\n"
        "> Can you check the queue depth?\n"
        "> It was 4100 earlier."
    )
    assert strip_quoted(body) == "Hi team, restarting the broker fixed it."


@pytest.mark.parametrize(
    "marker",
    [
        "----- Original Message -----",
        "-- Original Message --",
        "____________________________",
        "On 1 Jan 2026 at 09:00, A B <a@b.c> wrote:",
        "Sent from my iPhone",
    ],
)
def test_every_client_convention_is_recognised(marker):
    body = f"The new text.\n\n{marker}\nthe old conversation"
    assert strip_quoted(body) == "The new text."


def test_the_earliest_marker_wins():
    """A message may carry several. Cutting at the last would keep every
    quote before it."""
    body = (
        "New reply.\n"
        "On 2 Jan 2026 at 10:00, X <x@y.z> wrote:\n"
        "older\n"
        "----- Original Message -----\n"
        "oldest"
    )
    assert strip_quoted(body) == "New reply."


def test_angle_quoted_lines_go_even_without_a_header():
    body = "My answer.\n> their question\n> more of it"
    assert strip_quoted(body) == "My answer."


def test_a_message_that_is_entirely_quoted_reduces_to_nothing():
    """It contributed no new text. Recording it as though it did is how
    the same paragraph ends up in the graph thirty times."""
    assert strip_quoted("> only a quote\n> and nothing else") == ""


def test_ordinary_text_is_untouched():
    body = "The ActiveMQ broker stopped accepting connections on 61616."
    assert strip_quoted(body) == body


@pytest.mark.parametrize("body", [None, "", "   \n  "])
def test_empty_input_is_safe(body):
    assert strip_quoted(body) == ""


# --- cross-message dedup ------------------------------------------------------


def test_a_line_already_seen_in_the_thread_is_dropped():
    seen: set[str] = set()
    first = "The ActiveMQ broker on ae-app-prod-01 refused connections on port 61616."
    kept, removed = dedupe_against(first, seen)
    assert kept == first
    assert removed == 0

    kept2, removed2 = dedupe_against(first, seen)
    assert kept2 == ""
    assert removed2 > 0


def test_short_lines_are_never_suppressed():
    """Deduplicating greetings would strip "Thanks" from every reply,
    and a repeated sign-off is not the problem being solved."""
    seen: set[str] = set()
    dedupe_against("Thanks", seen)
    kept, _ = dedupe_against("Thanks", seen)
    assert kept == "Thanks"


def test_matching_ignores_case_and_whitespace():
    seen: set[str] = set()
    line = "The broker on ae-app-prod-01 refused connections on port 61616."
    dedupe_against(line, seen)
    kept, _ = dedupe_against("  THE BROKER ON AE-APP-PROD-01   refused connections on port 61616.  ", seen)
    assert kept == ""


def test_new_content_survives_alongside_repeated_content():
    seen: set[str] = set()
    old = "The broker on ae-app-prod-01 refused connections on port 61616."
    dedupe_against(old, seen)
    new = "Increasing the memory limit and restarting resolved it completely."
    kept, _ = dedupe_against(f"{old}\n{new}", seen)
    assert kept == new


# --- the two layers together --------------------------------------------------


def test_order_matters_the_original_survives_not_the_quote():
    """The single most important property. Walking a thread backwards
    would strip the ORIGINAL statement and keep the copy of it, which is
    worse than doing nothing: the graph would hold only quotations."""
    statement = "The queue depth was stuck at 4100 messages for over an hour."
    thread = [
        f"Reporting an issue.\n{statement}",
        f"Acknowledged, looking now.\n{statement}",
        f"Fixed by bouncing the service.\n{statement}",
    ]
    out = clean_thread_bodies(thread)
    assert statement in out[0]["body"]
    assert statement not in out[1]["body"]
    assert statement not in out[2]["body"]
    # And each reply keeps what it actually said.
    assert "Acknowledged, looking now." in out[1]["body"]
    assert "Fixed by bouncing the service." in out[2]["body"]


def test_the_original_is_reported_so_nothing_is_silently_lost():
    """Callers keep the raw body alongside, so a stripped message can be
    audited against what arrived."""
    out = clean_thread_bodies(["Answer.\n> the question they asked me"])[0]
    assert out["original_chars"] > out["kept_chars"]
    assert out["removed_chars"] == out["original_chars"] - out["kept_chars"]
    assert out["quote_stripped_chars"] > 0


def test_a_pure_quote_is_flagged_rather_than_hidden():
    out = clean_thread_bodies(["> nothing but a quote of the earlier message"])[0]
    assert out["body"] == ""
    assert out["is_quote_only"] is True


def test_an_empty_message_is_not_a_quote_only_message():
    """A message with no text never had anything to lose, and counting it
    as stripped would overstate what the stripping achieved."""
    assert clean_thread_bodies([""])[0]["is_quote_only"] is False


def test_a_thread_of_distinct_messages_loses_nothing():
    thread = [
        "The broker stopped accepting connections this morning at 09:12.",
        "Increasing the JVM memory limit and restarting resolved the fault.",
        "Confirmed the queue drained and agents resumed fetching jobs.",
    ]
    out = clean_thread_bodies(thread)
    assert [o["body"] for o in out] == thread
    assert all(o["removed_chars"] == 0 for o in out)


# --- reaching the standard pipeline, not just hydration -----------------------


def test_normalization_strips_quotes_for_every_source():
    """Most conversational evidence never passes through hydration —
    chat, email and work notes arrive from their connectors as
    individual items. Stripping only during hydration would leave every
    one of those carrying the whole prior conversation.
    """
    from contextedge.services.evidence_normalization import (
        evidence_body_from_payload,
    )

    payload = {"body": "Restarted the service.\n> did you try restarting it?"}
    assert evidence_body_from_payload(payload) == "Restarted the service."


def test_a_quote_only_message_is_marked_rather_than_repeated_or_emptied():
    """Two constraints pull in opposite directions here.

    The body must not be the quote: returning it re-embeds the whole
    conversation for a message that added nothing to it, which is the
    problem this module exists to solve.

    The body must also not be empty: the title falls back to a body
    snippet, so an unnameable evidence row is the alternative failure.

    A marker satisfies both. Dedup is unaffected because the content
    hash is taken over the RAW body, which is still distinct per
    message — collapsing every quote-only reply into one row is what an
    empty hash would have caused.
    """
    from contextedge.services.evidence_normalization import (
        QUOTED_ONLY_MARKER,
        evidence_body_from_payload,
        evidence_content_hash_from_payload,
    )

    first = {"body": "> only a quote"}
    second = {"body": "> a different quote entirely"}

    assert evidence_body_from_payload(first) == QUOTED_ONLY_MARKER
    assert evidence_body_from_payload(first).strip()
    assert evidence_content_hash_from_payload(
        first
    ) != evidence_content_hash_from_payload(second)


# --- delivery failures --------------------------------------------------------
#
# 31 of 303 real messages were Exchange non-delivery reports carrying
# 422,338 characters — 14% of the raw volume — of "Couldn't deliver the
# message", "How to Fix It" and remediation boilerplate.
#
# They also list the failed recipients' addresses, which identity
# extraction would mine into person entities: real people entering the
# graph as contacts because their mail bounced.


def test_a_non_delivery_report_is_recognised():
    from contextedge.services.thread_text_service import is_delivery_failure

    body = (
        "Unknown To address\n"
        "Couldn't deliver the message to the following recipients:\n"
        "someone@example.com\n"
        "How to Fix It\n"
        "The address might be misspelled or might not exist."
    )
    assert is_delivery_failure(body) is True


@pytest.mark.parametrize(
    "sender",
    ["Microsoft Outlook", "Mail Delivery Subsystem", "MAILER-DAEMON", "postmaster@x.com"],
)
def test_an_automated_sender_is_enough_on_its_own(sender):
    from contextedge.services.thread_text_service import is_delivery_failure

    assert is_delivery_failure("anything at all", sender) is True


def test_a_human_describing_a_mail_fault_is_kept():
    """The single most important negative case. For a company whose
    tickets are about integrations, "the alert email could not be
    delivered" is exactly the operational content this pipeline exists
    to capture — one phrase must not be enough to discard it."""
    from contextedge.services.thread_text_service import is_delivery_failure

    body = (
        "The alert email could not be delivered to the ops distribution "
        "list after the relay change, please check the connector."
    )
    assert is_delivery_failure(body) is False


def test_an_ordinary_reply_is_kept():
    from contextedge.services.thread_text_service import is_delivery_failure

    assert is_delivery_failure("Restarted the broker and the queue drained.") is False


def test_an_empty_body_is_not_a_bounce():
    from contextedge.services.thread_text_service import is_delivery_failure

    for body in (None, "", "   "):
        assert is_delivery_failure(body) is False


def test_a_bounce_is_dropped_whole_and_flagged():
    out = clean_thread_bodies(
        ["Couldn't deliver the message. How to Fix It: retype the address."],
        ["Microsoft Outlook"],
    )[0]
    assert out["body"] == ""
    assert out["is_delivery_failure"] is True
    assert out["removed_chars"] == out["original_chars"]


def test_a_bounce_does_not_poison_the_dedupe_memory():
    """If boilerplate claimed those lines, the same words would be
    suppressed when a human later wrote them."""
    shared = "The address might be misspelled or might not exist entirely."
    out = clean_thread_bodies(
        [f"Couldn't deliver the message. How to Fix It. {shared}", shared],
        ["Microsoft Outlook", "Priyanka Patil"],
    )
    assert out[0]["body"] == ""
    assert shared in out[1]["body"]


def test_senders_are_optional():
    """Callers that have no sender information still get quote handling."""
    out = clean_thread_bodies(["Answer.\n> quoted question"])
    assert out[0]["body"] == "Answer."
    assert out[0]["is_delivery_failure"] is False


# --- NDRs must reach the STANDARD pipeline, not only hydration ---------------
#
# Bounces do not only arrive inside hydrated threads. An email connector,
# an Exchange sync, or a ticket whose description is itself a bounce all
# go straight to normalization and never touch hydrate_thread.


def test_normalization_drops_a_bounce_for_every_source():
    from contextedge.services.evidence_normalization import (
        DELIVERY_FAILURE_MARKER,
        evidence_body_from_payload,
    )

    payload = {
        "from": "Microsoft Outlook",
        "body": "Unknown To address. Couldn't deliver the message to: a@b.com. How to Fix It",
    }
    assert evidence_body_from_payload(payload) == DELIVERY_FAILURE_MARKER


def test_the_failed_recipients_addresses_do_not_reach_the_graph():
    """They would otherwise be mined into person entities: real people
    entering the graph as contacts because their mail bounced."""
    from contextedge.services.evidence_normalization import evidence_body_from_payload

    payload = {
        "from": "Microsoft Outlook",
        "body": (
            "Couldn't deliver the message to the following recipients:\n"
            "manikanta@example.com, shiva@example.com\nHow to Fix It"
        ),
    }
    body = evidence_body_from_payload(payload)
    assert "manikanta@example.com" not in body
    assert "shiva@example.com" not in body


def test_a_bounce_is_reduced_to_a_marker_not_to_nothing():
    """The title falls back to a body snippet, so an empty body would
    leave the evidence unnameable in every list and search result."""
    from contextedge.services.evidence_normalization import evidence_body_from_payload

    body = evidence_body_from_payload(
        {"from": "MAILER-DAEMON", "body": "delivery has failed to these recipients"}
    )
    assert body.strip()


@pytest.mark.parametrize("key", ["from", "sender", "author", "email"])
def test_the_sender_is_read_from_whatever_key_a_connector_uses(key):
    from contextedge.services.evidence_normalization import (
        DELIVERY_FAILURE_MARKER,
        evidence_body_from_payload,
    )

    assert (
        evidence_body_from_payload({key: "Mail Delivery Subsystem", "body": "x"})
        == DELIVERY_FAILURE_MARKER
    )


# --- dedup identity must not move when cleaning rules change ------------------


def test_the_content_hash_ignores_cleaning():
    """If the hash reflected the stripped text, adding one quote marker
    to the pattern list would change the hash of every message
    containing it, and the next sync would re-ingest the lot as new."""
    from contextedge.services.evidence_normalization import (
        evidence_content_hash_from_payload,
        raw_body_from_payload,
    )
    import hashlib

    payload = {"body": "Answer.\n> quoted question"}
    expected = hashlib.sha256(
        raw_body_from_payload(payload).encode("utf-8")
    ).hexdigest()
    assert evidence_content_hash_from_payload(payload) == expected


def test_two_different_bounces_stay_distinct():
    """Cleaning reduces every bounce to the same marker. Hashing that
    would collapse every delivery failure in a source into one row."""
    from contextedge.services.evidence_normalization import (
        evidence_content_hash_from_payload,
    )

    one = {"from": "Microsoft Outlook", "body": "Couldn't deliver to a@b.com. How to Fix It"}
    two = {"from": "Microsoft Outlook", "body": "Couldn't deliver to x@y.com. How to Fix It"}
    assert evidence_content_hash_from_payload(one) != evidence_content_hash_from_payload(two)
