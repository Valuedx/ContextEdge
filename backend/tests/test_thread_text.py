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


# --- signatures and legal disclaimers -----------------------------------------
#
# Measured on 285 real messages that had ALREADY had quoted history removed:
# 79% still carried a sign-off, and 26% of the remaining text was signature
# blocks and disclaimers. Cross-message dedup does not catch them — it keeps
# the first occurrence per thread, so every thread retains one full copy.


def test_a_legal_disclaimer_is_cut_to_the_end():
    from contextedge.services.thread_text_service import strip_trailing_boilerplate

    body = (
        "The agent failed to register after the 8.2.5 upgrade.\n\n"
        "This e-mail and any attachments hereto are intended only for the use "
        "of the addressee(s) named herein and may contain confidential "
        "information. If you have received this e-mail in error, please notify "
        "the sender immediately and delete all copies."
    )
    out = strip_trailing_boilerplate(body)
    assert out == "The agent failed to register after the 8.2.5 upgrade."
    assert "confidential" not in out


def test_a_signature_block_is_removed_with_its_contact_details():
    """A signature is attached to a person, so leaving it in seeds identity
    extraction with the sender's name, title, phone and employer on every
    message they ever sent."""
    from contextedge.services.thread_text_service import strip_trailing_boilerplate

    body = (
        "Please verify the ownership of the web driver directory.\n\n"
        "Thanks and Regards,\n\nDana Reed\nSupport Engineer\n"
        "Mobile: +91 98765 43210\nwww.example.com"
    )
    out = strip_trailing_boilerplate(body)
    assert out == "Please verify the ownership of the web driver directory."
    assert "98765" not in out
    assert "Dana Reed" not in out


def test_a_sign_off_followed_by_more_content_is_left_alone():
    """The conservative half. "Thanks," is regularly followed by more
    substance, and eating a paragraph of diagnosis costs far more than
    keeping a name."""
    from contextedge.services.thread_text_service import strip_trailing_boilerplate

    body = (
        "Restarted the service.\n\nThanks\n\n"
        "One more thing worth flagging: the agent is still failing to register "
        "against the QA gateway, and the RADIUS timeouts in the log look like "
        "the same fault we saw in April. Could you check whether the firewall "
        "rule was reapplied after the maintenance window?"
    )
    assert strip_trailing_boilerplate(body) == body.strip()


def test_thanks_inside_a_sentence_is_not_a_sign_off():
    from contextedge.services.thread_text_service import strip_trailing_boilerplate

    body = "Thanks for confirming the port was open — that rules out the firewall."
    assert strip_trailing_boilerplate(body) == body


def test_the_last_sign_off_is_used_not_the_first():
    """A message may thank someone mid-conversation and continue; only the
    final sign-off plausibly begins the signature."""
    from contextedge.services.thread_text_service import strip_trailing_boilerplate

    body = (
        "Thanks\n\nThe logs are attached and show the SSL handshake failing "
        "at the point the client presents its certificate to the gateway.\n\n"
        "Regards,\nDana"
    )
    out = strip_trailing_boilerplate(body)
    assert "SSL handshake" in out
    assert not out.rstrip().endswith("Dana")


def test_a_message_that_is_only_a_signature_is_never_emptied():
    """A body reduced to nothing leaves the evidence unnameable, and a
    message consisting solely of a sign-off is still a real event with a
    real author."""
    from contextedge.services.thread_text_service import strip_trailing_boilerplate

    body = "Thanks and Regards,\n\nDana Reed\nSupport Engineer"
    assert strip_trailing_boilerplate(body).strip()


def test_boilerplate_rules_name_no_organisation():
    """A rule listing one customer's company name silently does nothing for
    every other tenant while appearing to work.

    Scoped to the patterns themselves — the module docstring cites the
    corpus the thresholds were measured on, which is a provenance note,
    not a matching rule.
    """
    from contextedge.services.thread_text_service import (
        _CONTACT,
        _DISCLAIMER,
        _SIGNOFF,
    )

    rules = " ".join(p.pattern.lower() for p in (_DISCLAIMER, _SIGNOFF, _CONTACT))
    for banned in ("automationedge", "acme", "zoho", "hdfc", "idfc", "servicenow"):
        assert banned not in rules


def test_boilerplate_is_removed_before_the_dedup_memory_sees_it():
    """A signature suppressed as a duplicate would still have claimed its
    lines in `seen`, so the same words written later by a person would be
    dropped as a repeat of a footer."""
    from contextedge.services.thread_text_service import clean_thread_bodies

    shared = "Please feel free to reach us at the links mentioned below today."
    out = clean_thread_bodies(
        [
            f"The agent is down.\n\nRegards,\nDana\n{shared}\nwww.example.com",
            # A person writes the same sentence as real content later.
            f"{shared}",
        ],
        ["Dana", "Sam"],
    )
    assert "reach us at the links" not in out[0]["body"]
    assert shared in out[1]["body"]


def test_the_standard_pipeline_strips_boilerplate_too():
    """Most conversational evidence never passes through hydration."""
    from contextedge.services.evidence_normalization import evidence_body_from_payload

    body = evidence_body_from_payload(
        {"body": "Gateway timed out.\n\nBest Regards,\nDana Reed\nMobile: +1 555 0100"}
    )
    assert body == "Gateway timed out."


@pytest.mark.parametrize(
    "header",
    [
        "On 3 August 2026 at 14:02, Jane Doe <j@x.com> wrote:",
        '---- on Mon, 27 Jul 2026 23:58:50 +0530 "Jane Doe"<j@x.com> wrote ----',
        "---- On Tue, 21 Jul 2026 11:21:41 +0530 Jane Doe <j@x.com> wrote ----",
        "on 1 Jan 2026 at 09:00, A B <a@b.c> wrote",
    ],
)
def test_every_attribution_shape_is_recognised(header):
    """Capital "On" and a mandatory colon was the original rule, and it
    missed the dashed lowercase variant on 127 of 285 already-"cleaned"
    messages — 43,136 characters, a quarter of the corpus, still carrying
    the conversation the stripping was supposed to remove."""
    body = f"My new reply.\n\n{header}\n> the whole prior conversation"
    assert strip_quoted(body) == "My new reply."


@pytest.mark.parametrize(
    "line",
    [
        "As discussed on Monday, the engineer wrote a workaround",
        "I read what he wrote",
        "The script wrote 4,100 rows to the queue table",
    ],
)
def test_prose_about_writing_is_not_an_attribution_line(line):
    """The start anchor is what makes the looser pattern safe: prose
    mentioning what someone wrote does not begin with "on"."""
    body = f"{line}\nand the fault cleared after a restart."
    assert strip_quoted(body) == body


def test_a_trailing_link_footer_is_removed():
    """"Follow us" rows and portal menus. Measured at 11.4% of what
    survived the signature and disclaimer rules, the same footer
    appearing 116 times."""
    from contextedge.services.thread_text_service import strip_trailing_boilerplate

    body = (
        "The agent registered successfully after the firewall rule was reapplied.\n\n"
        "Please feel free to reach us at the links mentioned.\n\n"
        "https://example.com | Home | https://community.example.com | My Area"
    )
    out = strip_trailing_boilerplate(body)
    assert out == "The agent registered successfully after the firewall rule was reapplied."


def test_short_closing_lines_without_a_link_are_kept():
    """A run of short lines with no link is a closing sentence, not a
    footer — cutting it can take the last line of an answer."""
    from contextedge.services.thread_text_service import strip_trailing_boilerplate

    body = "We traced the fault to the gateway.\n\nIt is fixed now.\n\nPlease confirm."
    assert strip_trailing_boilerplate(body) == body


def test_a_link_inside_the_answer_is_not_a_footer():
    """A URL is often the answer — a KB link, a build, a log location."""
    from contextedge.services.thread_text_service import strip_trailing_boilerplate

    body = (
        "Download the patched driver from https://example.com/builds/8.2.6 and "
        "restart the agent, then confirm the version reported in the console "
        "matches what the installer wrote to the manifest file."
    )
    assert strip_trailing_boilerplate(body) == body


def test_footer_rules_survive_an_empty_or_missing_body():
    from contextedge.services.thread_text_service import strip_trailing_boilerplate

    assert strip_trailing_boilerplate(None) == ""
    assert strip_trailing_boilerplate("") == ""
    assert strip_trailing_boilerplate("   \n  ") == ""


def test_a_single_closing_link_is_not_a_menu():
    """Two links make a menu; one makes a sentence. A closing line
    pointing at a KB article or a build IS the answer."""
    from contextedge.services.thread_text_service import strip_trailing_boilerplate

    body = "Fixed in 8.2.6.\n\nRelease notes: https://example.com/notes"
    assert strip_trailing_boilerplate(body) == body
