"""What is allowed to become a canonical identity.

The identity table is a resolution space: rows exist so that two mentions of
the *same real thing* in different documents collapse to one node. Everything
in it costs an LLM adjudication against its trigram-similar neighbours, and
every wrong row costs that forever after.

On the live Zoho corpus, 134 evidence items produced **605 canonical
identities** — 4.5 per ticket — and identity work was 78% of all model spend.
The table it produced was not an identity space:

    service     | the project, screenshot, customer query, query timeout,
                | server, relay server, client 8.2.5, 8.4.0 release,
                | Workflow Request Not Executing in Production issue
    environment | India, India, India, Production, T3, UAT, Brazil
    patch       | 8.5.0, ondemand patch update
    vendor      | client, Certificate Authority, Cognizant

Two independent defects, and a fix for either alone leaves the other:

**1. Types that are facets, not identities.** `environment`, `version`,
`patch` and `vendor` describe *an attribute of* an incident. "Production" is
not a thing whose mentions need resolving to one node — it is a value, and
one this pipeline already derives deterministically from the source's own
custom fields (see ``source_facets``). Adjudicating them is spend with no
product: "India" was adjudicated three times and stored three times anyway.

**2. Names that are not names.** The bigger share by volume. "the project"
and "query timeout" are common-noun phrases the extractor labelled `service`;
"Workflow Request Not Executing in Production issue" is a ticket subject.
Length alone does not separate them — at one and two words `service` holds
both `NewWorkflowQueue`, `BROKER1`, `MFA` and `screenshot`, `server`.
What separates them is *shape*: a real system name carries a proper-noun or
identifier signal — a capital past the first letter, an all-caps token, an
internal dot/underscore/hyphen/slash — while a description is lowercase
running English (or Portuguese: the corpus contains "Execution Metrics no
Portal do AE não abre issue", which is why the rules here are structural and
not a list of English stop-words).

**A strong identifier overrides both rules.** An entity carrying an email,
FQDN, hostname, serial or IP is identity-bearing whatever its label or
wording — that is the one signal more reliable than the type the model chose.

This gate runs *before* candidate generation, so a rejected entity costs no
adjudication call at all. It is deliberately separate from the prompt: a
prompt tells the model what to return, this decides what the graph accepts,
and defence in depth means a model that ignores its instructions — or a new
model version with different habits — still cannot pollute the table.
"""

from __future__ import annotations

import re

import structlog

logger = structlog.get_logger()

# Types whose mentions are worth resolving to a shared node.
#
# `application` and `service` stay in despite being the noisiest sources of
# junk above: the noise is bad *names*, which rule 2 handles, and dropping
# the types would throw away every real named system with them
# (NewWorkflowQueue, SystemResourceMonitor, Quartz).
IDENTITY_BEARING_TYPES = frozenset({"person", "device", "application", "service"})

# Types that describe an incident rather than name a participant in it.
#
# These are NOT discarded information — they are information with a better
# home, and the distinction matters because this graph is queried by agents
# resolving incidents, where "does this fix apply to 8.4.0?" and "is this
# the same customer's environment?" are decisive questions.
#
# Where each one is actually captured, measured on the live corpus:
#
#   version      tickets:  `source_facets.version` from the source's own
#                          custom field — 99/99 tickets, 29 distinct values,
#                          typed by the engineer who closed the ticket
#                articles: `evidence.applicability.versions`, via
#                          `applicability_from_facets` or the dedicated
#                          knowledge-applicability extractor
#   environment  tickets:  `source_facets.environment` ("T3") — 99/99
#                articles: `evidence.applicability.environments`
#   vendor       tickets:  `source_facets.customer` — 93/99, 24 distinct
#
# All of those are queryable fields. A canonical identity row is not a
# better home for them: "8.2.5" as a node gets adjudicated against "8.2"
# and "8.5.0" forever, and answers no question that the facet does not
# answer more precisely, because a version is only meaningful attached to
# a product. What this gate removes is the duplicate, not the fact.
#
# Residual, stated so it is not rediscovered as a surprise: a version that
# appears ONLY in thread prose ("we upgraded to 8.4.0 and it broke") while
# the custom field says something else is not promoted to a facet. It stays
# in the evidence body, so retrieval still finds it; it is not filterable.
FACET_TYPES = frozenset({"environment", "version", "patch", "vendor"})

# Above this a string is a description, not a name. Measured against the
# live table: every genuine person, device and system name sits at four
# words or fewer, while the phrases start at five and run to nine
# ("server-side validation rules (restvalidation.json) for Email
# Configuration module"). Set at the boundary, not beyond it.
MAX_NAME_WORDS = 4

# Quotes and apostrophes mark reported speech or UI text being described
# ("Click 'View Documents' issue", "DocEdge plugin's error handling"),
# never a system's own name.
_QUOTE_CHARS = "\"'`‘’“”"

# Characters that appear inside identifiers and effectively never inside
# prose words: support.automationedge.com, EBT_Card_Shadow_Credit,
# WS-Federation, x_aetp_ae500, ORDERS/DB.
_IDENTIFIER_CHARS = re.compile(r"[._/\\@]|(?<=\w)-(?=\w)")

_HAS_LETTER = re.compile(r"[^\W\d_]", re.UNICODE)

# Identifier kinds strong enough to override BOTH rules above.
#
# Each of these names one specific machine, mailbox or physical asset, so an
# entity carrying one is identity-bearing however the model labelled it and
# however its display text reads.
#
# `external_id` is deliberately NOT among them, and that omission was
# measured. The extractor invents `source_identifiers` from prose, and the
# normalizer records each one as an `external_id` alias — so the live corpus
# produced canonical identities for the versions "11.0.6" and "8.2.0" whose
# only claim to being identities was an external id keyed on the strings
# "apache tomcat" and "onprem". An id is only as authoritative as whatever
# issued it, and nothing issued those.
#
# This costs no real lookup: a genuine connector-issued external id still
# matches at layer 1, which runs BEFORE this gate. Excluding it here only
# stops such an id from CREATING a new facet-type identity.
OVERRIDING_IDENTIFIERS = frozenset(
    {"email", "fqdn", "hostname", "serial_number", "ip_address"}
)


def _is_name_token(token: str) -> bool:
    """Does this single word carry a proper-noun or identifier signal?

    Requires a letter somewhere: "8.2.5" and "5360" are full of identifier
    punctuation but name a version and a request, not a thing.
    """
    if not _HAS_LETTER.search(token):
        return False
    if _IDENTIFIER_CHARS.search(token):
        return True
    letters = [c for c in token if c.isalpha()]
    if not letters:
        return False
    # All-caps (MFA, BROKER1) or any capital after the first letter
    # (NewWorkflowQueue, DocEdge) — both are signals prose does not give.
    if len(letters) > 1 and all(c.isupper() for c in letters):
        return True
    if any(c.isupper() for c in letters[1:]):
        return True
    # A leading capital counts, but only that: it is the weakest signal
    # here — every sentence starts with one — so it is accepted at the
    # token level and paid for by the length limit at the name level.
    return letters[0].isupper()


def looks_like_a_name(name: str) -> bool:
    """True when the string reads as something's name rather than about it."""
    text = name.strip()
    # One character resolves against everything and identifies nothing. The
    # live table contains a person called "A".
    if len(text) < 2:
        return False
    if any(ch in text for ch in _QUOTE_CHARS):
        return False
    tokens = text.split()
    if len(tokens) > MAX_NAME_WORDS:
        return False
    return any(_is_name_token(token) for token in tokens)


def identity_rejection_reason(entity) -> str | None:
    """Why this extracted entity may not become an identity, or None.

    Takes a ``NormalizedEntity``. Returns a short stable reason string so
    the caller can count rejections by cause without re-deriving them.
    """
    # A strong identifier settles it: an email or FQDN names one thing
    # whatever the model called it, and whatever the display text reads
    # like. Checked first so neither rule below can override it.
    if any(alias_type in OVERRIDING_IDENTIFIERS for alias_type, _, _ in entity.strong_identifiers):
        return None
    if entity.entity_type in FACET_TYPES:
        return "facet_type"
    if entity.entity_type not in IDENTITY_BEARING_TYPES:
        return "unsupported_type"
    if not looks_like_a_name(entity.display_name):
        return "not_a_name"
    return None
