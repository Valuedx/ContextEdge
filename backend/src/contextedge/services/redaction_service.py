"""Regex-based PII/secret redaction at ingest.

Runs on the normalize path before embedding + LLM extraction so that
PII / credentials never leave the tenant boundary. Kept deliberately
simple (regex only) — a Presidio integration is a follow-up once the
customer has a performance profile to measure against.

Patterns are purposefully conservative: over-redaction is preferable
to under-redaction. The cost of a false positive (an unrelated number
looking like a credit card) is one masked token in a downstream
episode; the cost of a false negative is a live credit card / API key
shipped to an LLM provider.

Public surface:

- ``redact(text)`` — return ``(redacted_text, counts_by_kind)``.
- ``redact_evidence_fields(title, body)`` — convenience pair call used
  by the normalize worker.
"""

from __future__ import annotations

import re

# Replacement template. Callers who want to strip redacted regions
# entirely can post-process, but we keep the placeholder visible so
# reviewers can tell redaction happened rather than assuming the field
# was empty.
_PLACEHOLDER = "[REDACTED:{kind}]"


# Ordered rules — more specific patterns first so the longer /
# higher-confidence match wins. Each entry is ``(kind, compiled_re)``.
# Kind names are used as both telemetry keys and the token substitution
# so they must stay stable once a tenant relies on them in reports.
_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    # --- Secrets first ------------------------------------------------
    #
    # ORDER IS LOAD-BEARING. These run before PHONE and CREDIT_CARD
    # because a numeric rule that fires *inside* a secret leaves the rest
    # of it in place: a GitHub token was being rewritten to
    # "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ[REDACTED:PHONE]", which reads as
    # redacted and is not. A partly-redacted secret is worse than an
    # obviously-unredacted one, because it passes review.
    #
    # These were added when validating redaction over image-derived text:
    # the figure pass reads config files and terminals out of
    # screenshots, and logs carry the same content. Tokens and
    # password assignments are precisely what lives there, and nothing
    # matched them.
    (
        "API_TOKEN",
        re.compile(
            r"\b("
            r"gh[pousr]_[A-Za-z0-9]{16,255}"          # GitHub
            r"|xox[baprs]-[A-Za-z0-9\-]{10,}"          # Slack
            r"|sk-[A-Za-z0-9_\-]{16,}"                 # OpenAI-style
            r"|glpat-[A-Za-z0-9_\-]{16,}"              # GitLab
            r"|AIza[A-Za-z0-9_\-]{20,}"                # Google API key
            r"|ya29\.[A-Za-z0-9_\-]{20,}"              # Google OAuth
            r")\b"
        ),
    ),
    (
        "JWT",
        re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\b"),
    ),
    (
        "BEARER_TOKEN",
        re.compile(r"(?i)\b(?:bearer|authorization:\s*bearer)\s+[A-Za-z0-9._\-+/=]{12,}"),
    ),
    # key=value secrets in config files, connection strings, and CLI
    # lines — the dominant shape in a screenshotted config. The key name
    # is kept so the reader still knows what was removed.
    (
        "SECRET_ASSIGNMENT",
        re.compile(
            r"(?i)\b(password|passwd|pwd|secret|api[_\-]?key|access[_\-]?token"
            r"|auth[_\-]?token|client[_\-]?secret|private[_\-]?key)"
            r"\s*[:=]\s*"
            r"[\"']?([^\s\"'&;,)]{4,})[\"']?"
        ),
    ),
    (
        "EMAIL",
        re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"),
    ),
    # E.164-ish phone: optional +CC, optional parens / dashes / spaces.
    #
    # The boundaries exclude any WORD character, not just digits. Guarding
    # on `(?<!\d)` alone let a ten-digit run inside a hex identifier match:
    # the external id "a9c0c8d2c2895551234f7705562f9cb0" was stored as
    # "a9c0c8d2c[REDACTED:PHONE]f7705562f9cb0". That is worse than a
    # cosmetic problem — external ids are Layer 1 strong identifiers, so a
    # corrupted one silently stops matching and the identity forks on
    # every future mention. Serial numbers and hex blobs were hit the same
    # way.
    #
    # Deliberately still permissive about separators: hyphens, spaces,
    # dots and parens remain inside and around the match, so every real
    # formatting of a phone number is still caught. Only adjacency to a
    # LETTER is treated as evidence that the digits belong to a larger
    # token. For a privacy control, failing to redact is the worse
    # direction, so the guard narrows what counts as "inside an
    # identifier" rather than narrowing what counts as a phone number.
    (
        "PHONE",
        re.compile(
            r"(?<!\w)"
            r"(?:\+\d{1,3}[\s.\-]?)?"
            r"(?:\(\d{3}\)|\d{3})[\s.\-]?\d{3}[\s.\-]?\d{4}"
            r"(?!\w)"
        ),
    ),
    (
        "SSN",
        re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    ),
    # Credit-card-ish: 13-19 digits optionally grouped with dashes or
    # spaces. No Luhn validation — kept permissive on purpose. This
    # will occasionally match phone numbers that slipped past the PHONE
    # rule above; the PHONE rule runs first so that's rare.
    (
        "CREDIT_CARD",
        re.compile(r"\b(?:\d[ \-]?){13,19}\b"),
    ),
    # AWS access key IDs.
    (
        "AWS_ACCESS_KEY",
        re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    ),
    # AWS secret access keys (40 chars, base64-ish). Low FP rate; the
    # 40-char length + charset is distinctive.
    (
        "AWS_SECRET_KEY",
        re.compile(r"(?<![A-Za-z0-9/+=])[A-Za-z0-9/+=]{40}(?![A-Za-z0-9/+=])"),
    ),
    # Private-key headers — we blast the whole block if a header is
    # present. Use DOTALL so the replacement spans newlines.
    (
        "PRIVATE_KEY",
        re.compile(
            r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----"
            r".*?"
            r"-----END (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----",
            re.DOTALL,
        ),
    ),
)


def redact(text: str | None, *, enabled: bool = True) -> tuple[str | None, dict[str, int]]:
    """Replace PII / secret spans in ``text`` with ``[REDACTED:{kind}]``.

    Returns ``(redacted_text, counts)``. ``counts`` maps each rule name
    that fired at least once to the number of substitutions, so the
    caller can surface per-kind metrics / operational-event payloads
    without re-running the regex.

    When ``enabled=False`` this is a no-op that returns the input
    unchanged with an empty count dict — makes it cheap for callers to
    gate redaction behind a per-tenant flag without branching at every
    call site.
    """
    if not text or not enabled:
        return text, {}

    counts: dict[str, int] = {}
    out = text
    for kind, pattern in _RULES:
        def _sub(match: re.Match[str], _kind: str = kind) -> str:
            counts[_kind] = counts.get(_kind, 0) + 1
            return _PLACEHOLDER.format(kind=_kind)

        out = pattern.sub(_sub, out)
    return out, counts


def redact_evidence_fields(
    title: str | None,
    body: str | None,
    *,
    enabled: bool = True,
) -> tuple[str | None, str | None, dict[str, int]]:
    """Redact ``title`` + ``body`` in a single call. Counts are merged."""
    redacted_title, title_counts = redact(title, enabled=enabled)
    redacted_body, body_counts = redact(body, enabled=enabled)
    merged: dict[str, int] = dict(title_counts)
    for kind, count in body_counts.items():
        merged[kind] = merged.get(kind, 0) + count
    return redacted_title, redacted_body, merged
