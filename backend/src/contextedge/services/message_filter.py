"""Which thread messages are worth a model call.

Hydration expands one Zoho ticket into ~41 message rows, and the live corpus
holds **18,410** of them. Each one that reaches normalization costs a
relevance classification at minimum, and a full identity/decision fan-out if
it passes — measured at **4,539 tokens per message-derived evidence item**,
or ~82.8M tokens for the corpus. That is six times everything the 1,486
tickets and articles cost put together.

The spend would be justified if the content were, but sampling says
otherwise. 54% of messages are under 150 characters, and at that length they
are coordination rather than diagnosis:

    "Hi Team, Any update?"
    "Looping in @Anjana Swami."
    "As discussed on the call, have you checked this with the Appsec team?"
    "Hello Shubham, We will check your issue internally and schedule a call."

Paying a ~600-token relevance call to discover each of those is noise is
~11M tokens spent to reject chatter. This gate is deterministic, runs before
any model call, and costs nothing.

**Messages are not redundant with their ticket** — that was checked, not
assumed. Ticket bodies average 532 characters and hold the description and
resolution fields, not the conversation. The diagnostic back-and-forth exists
only in these rows, which is exactly why the filter has to be careful rather
than aggressive.

Hence the escape hatch. Length alone would discard "Restart the AE service on
T3" (28 chars), which is a real fix. A short message survives when it carries
a technical signal — an error code, a path, a version, a host, an identifier.
The rule is therefore *short AND featureless*, never short alone.

Nothing is deleted: the raw object is still stored and the message is still
part of its hydrated thread. This decides only whether the message becomes
its own evidence item with its own model spend.
"""

from __future__ import annotations

import re
from typing import Any

import structlog

logger = structlog.get_logger()

# Below this, a message needs to show a technical signal to earn a model
# call. Set at the top of the coordination band measured on the corpus:
# 8,750 of 16,215 inline messages fall under it, and the samples at
# 50-149 chars are meetings, greetings and status pings without exception.
MIN_DIAGNOSTIC_CHARS = 150

# Signals that a short message is operational rather than social. Any one
# of these keeps the message regardless of length.
_TECHNICAL_SIGNALS = (
    # Error and status codes: 0x8007007E, ORA-01555, HTTP 500, exit code 3
    re.compile(r"\b0x[0-9a-f]{4,}\b", re.I),
    re.compile(r"\b[A-Z]{2,}-\d{3,}\b"),
    re.compile(r"\b(?:error|status|exit)\s*(?:code)?\s*[:=#]?\s*\d{3,}\b", re.I),
    # Filesystem paths and files with an extension
    re.compile(r"[A-Za-z]:\\[^\s]+|/(?:etc|var|opt|usr|home)/[^\s]+"),
    re.compile(r"\b[\w.-]+\.(?:log|conf|xml|json|jar|dll|exe|sh|bat|properties|yml|yaml)\b", re.I),
    # Versions and builds: 8.2.5, v4.6
    re.compile(r"\bv?\d+\.\d+(?:\.\d+)+\b"),
    # Hosts, URLs, IPs, emails
    re.compile(r"\bhttps?://\S+"),
    re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
    re.compile(r"\b[\w.-]+@[\w.-]+\.\w{2,}\b"),
    re.compile(r"\b[a-z0-9-]+\.(?:com|net|org|io|local|corp)\b", re.I),
    # Identifier-shaped tokens: CamelCase, SCREAMING_SNAKE, dotted properties
    re.compile(r"\b[a-z]+(?:[A-Z][a-z]+){2,}\b"),
    re.compile(r"\b[A-Z][A-Z0-9]{2,}_[A-Z0-9_]{2,}\b"),
    re.compile(r"\b\w+\.\w+\.\w+\b"),
    # Stack traces, SQL, shell
    re.compile(r"\b(?:Exception|Traceback|at\s+\w+\.\w+\(|caused by)\b", re.I),
    re.compile(r"\b(?:select|insert|update|delete)\s+.*\b(?:from|into|set|where)\b", re.I),
    re.compile(r"\b(?:systemctl|service|net\s+stop|net\s+start|kill\s+-9|chmod|chown)\b", re.I),
)

NOISE_REASONS = ("delivery_failure", "quote_only", "empty", "coordination_only")

# Bump on ANY change to the rules below — thresholds, signal patterns,
# markup or signature handling.
#
# This gate decides that a message never becomes evidence, and it leaves no
# row behind when it does: an unfiltered message and one rejected by a rule
# we later decide was wrong look identical afterwards. The version is what
# makes that recoverable. Because the filter is a pure function of the
# stored payload, re-assessment is exact rather than approximate — every
# message rejected by an older version can be re-judged by re-running the
# current one over the raw objects that have no evidence:
#
#     select r.* from raw_evidence_objects r
#     where r.external_id like '%:msg:%'
#       and not exists (select 1 from evidence_items e
#                       where e.raw_object_ref = r.id)
#
# Nothing is deleted, so nothing needs re-fetching from the source — the
# raw rows are all still there. The same discipline the repo applies to
# prompts (immutable, versioned) and chunks (`chunker_version`): a
# component whose output shape depends on its rules must say which rules
# produced it.
#
# v1 (2026-08-17): initial gate. Connector flags, empty bodies, and
#   short-and-featureless coordination at MIN_DIAGNOSTIC_CHARS=150,
#   measured against 18,907 live messages (47% rejected).
MESSAGE_FILTER_VERSION = "v1"

# Source markup that is identifier-SHAPED but carries no engineering content,
# stripped before the signal test. Zoho encodes an @-mention as
# `zsu[@user:60051092952]zsu`, which trips the dotted-token and
# SCREAMING_SNAKE patterns and kept messages that read, in full,
# "zsu[@user:...]zsu please look into this". A mention names a colleague,
# not a system.
_MARKUP_NOISE = (
    re.compile(r"zsu\[@user:\d+\]zsu", re.I),   # Zoho @-mention
    re.compile(r"\[cid:[^\]]+\]", re.I),        # inline-image content ids
    re.compile(r"\[image:[^\]]*\]", re.I),
)


# Signature blocks, which inflate length without adding content. Sampling
# the 150-399 band found it roughly half genuine and half one-line replies
# wearing a corporate footer: "Please could you share an update on the
# issue?" is 35 characters of content followed by 126 of "Regards, Srujan |
# RPA Support / AutomationEdge Technologies / Follow Us:". Measuring length
# after the cut is what separates that from a real 210-character answer,
# and it is strictly better than raising the threshold, which would discard
# both.
#
# Everything from the first marker to the end of the body is dropped: a
# signature is terminal by construction, so there is nothing after it to
# lose.
_SIGNATURE_MARKERS = re.compile(
    r"(?im)^\s*(?:"
    r"regards|best regards|kind regards|warm regards|thanks (?:&|and) regards|"
    r"atenciosamente|saludos|cordialement|mit freundlichen|"
    r"follow us\s*:|"
    r"disclaimer\s*:|"
    r"this (?:e-?mail|message) (?:and any attachments )?is confidential"
    r")\b.*",
    re.MULTILINE,
)

# A support desk's own footer line, which appears with or without a
# preceding "Regards".
_FOOTER_LINE = re.compile(
    r"(?im)^\s*[^\n]{0,60}\|\s*(?:rpa|it|service|technical)\s+support\b.*$"
)


def strip_markup(text: str) -> str:
    """Remove source markup that mimics a technical signal."""
    for pattern in _MARKUP_NOISE:
        text = pattern.sub(" ", text)
    return text


def strip_signature(text: str) -> str:
    """Cut a trailing signature block so length measures content."""
    match = _SIGNATURE_MARKERS.search(text)
    if match:
        text = text[: match.start()]
    return _FOOTER_LINE.sub(" ", text)


def has_technical_signal(text: str) -> bool:
    """Does this text contain something an engineer could act on?"""
    cleaned = strip_markup(text)
    return any(pattern.search(cleaned) for pattern in _TECHNICAL_SIGNALS)


def message_noise_reason(payload: dict[str, Any] | None) -> str | None:
    """Why this message should not become its own evidence item, or None.

    Only applies to hydrated thread messages; the caller decides that.
    """
    if not isinstance(payload, dict):
        return None

    # The connector already flags these; they never carry diagnosis.
    if payload.get("is_delivery_failure") is True:
        return "delivery_failure"
    if payload.get("is_quote_only") is True:
        return "quote_only"

    body = payload.get("body")
    text = str(body).strip() if body else ""
    if not text:
        return "empty"

    # Length is measured on CONTENT: markup and signature stripped. A
    # two-word reply carrying three @-mentions and a corporate footer is
    # not a long message, and counting either toward the threshold let
    # exactly those through.
    meaningful = strip_signature(strip_markup(text)).strip()
    if not meaningful:
        return "empty"

    # Short AND featureless. Never short alone: "Restart the AE service on
    # T3" is 28 characters and is the entire fix.
    if len(meaningful) < MIN_DIAGNOSTIC_CHARS and not has_technical_signal(text):
        return "coordination_only"

    return None


def is_hydrated_message(payload: dict[str, Any] | None) -> bool:
    """True for a message row produced by thread hydration."""
    if not isinstance(payload, dict):
        return False
    return payload.get("_connector_object_type") == "hydrated_message"
