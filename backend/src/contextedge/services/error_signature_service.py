"""Deterministic error-signature fingerprints (diagnosis roadmap D1).

An exact fingerprint match is the highest-precision join between a new
incident and history: "is this exact failure known?" answered in one
indexed lookup, with the episodes behind it one hop away. The
``error_signatures`` table and its ``maf.v1`` hydration have existed
since the schema shipped — this service is the missing populator.

Everything here is pure normalization: no LLM call, no embedding, so it
runs on every ingested evidence item at effectively zero marginal cost.
Precision is deliberately favoured over recall — a junk signature poisons
exact-match lookups in a way a missed one does not — so extraction only
fires on unambiguous error shapes (exception classes, vendor error
codes, ERROR/FATAL log lines), not on conversational uses of "failed".
"""

from __future__ import annotations

import re
import uuid

import structlog
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.models.error_signature import ErrorSignature

logger = structlog.get_logger()

# Bound the scan so a pasted 100k-char thread costs microseconds, not
# milliseconds. Error shapes worth fingerprinting repeat; the first 500
# lines of any log excerpt carry them.
MAX_SCAN_CHARS = 40_000
MAX_SCAN_LINES = 500
MAX_SIGNATURES_PER_EVIDENCE = 3
MAX_EXAMPLES_PER_SIGNATURE = 3
EXAMPLE_CHARS = 300
DISPLAY_NAME_CHARS = 200

# Tier-1 shapes: wrong to ignore in any corpus, near-impossible to emit
# conversationally.
_EXCEPTION_RE = re.compile(
    r"\b([A-Z][A-Za-z0-9]{2,60}(?:Exception|Error|Fault))\b"
)
_VENDOR_CODE_RE = re.compile(
    r"\b(ORA-\d{3,6}|SQLSTATE\s*\[?\w{5}\]?|0x[0-9A-Fa-f]{4,16}|errno\s*[=:]?\s*\d{1,5})\b"
)
_HTTP_5XX_RE = re.compile(r"\b(?:HTTP|status(?:\s+code)?)[\s:]*([5]\d\d)\b", re.IGNORECASE)
# Log-severity lines: `2026-08-07 ... ERROR com.foo.Bar: message` and
# similar. Anchored to the severity token so prose containing the word
# "error" mid-sentence does not match.
_LOG_LEVEL_RE = re.compile(r"(?:^|[\s\[])(?:ERROR|FATAL|SEVERE)(?:[\]\s:]|$)")

# Variable stripping. Order matters: UUIDs before hex, dates/times before
# the generic digit-run rule would shred them into key-polluting fragments.
_NORMALIZERS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
            r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
        ),
        "<id>",
    ),
    (re.compile(r"\b0x[0-9a-fA-F]{4,16}\b"), "<hex>"),
    (re.compile(r"\b\d{4}-\d{2}-\d{2}\b"), "<date>"),
    (re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?(?:[.,]\d{1,6})?\b"), "<time>"),
    (
        re.compile(r"\b(?:UTC|GMT|IST|EST|EDT|PST|PDT|CET|CEST)(?:[+-]\d{1,2}(?::\d{2})?)?\b"),
        "<tz>",
    ),
    (re.compile(r"(?:[A-Za-z]:)?[\\/](?:[\w.-]+[\\/])+[\w.-]+"), "<path>"),
    (re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(?::\d+)?\b"), "<host>"),
    (re.compile(r"\[REDACTED:[A-Z_]+\]"), "<redacted>"),
    (re.compile(r"'[^']{1,120}'|\"[^\"]{1,120}\""), "<str>"),
    # Long digit runs are instance data (ports, sizes, record counts).
    # Short ones stay: "TLS 1.2" and "version 8" are diagnostic identity.
    (re.compile(r"\b\d{3,}\b"), "<n>"),
)

_STOPWORDS = frozenset(
    "the a an at in on of for to from with and or is was were has have had "
    "this that it its while when after before during".split()
)
_TOKEN_RE = re.compile(r"[a-z0-9<>._]+")


def normalize_error_line(line: str) -> str:
    """Strip instance data so two occurrences of the same failure — with
    different ids, hosts, paths, sizes — normalize identically."""
    out = " ".join(line.split())
    for pattern, replacement in _NORMALIZERS:
        out = pattern.sub(replacement, out)
    return out.strip()[:500]


def _classify_line(line: str) -> str | None:
    """Return an error_type when the line is an unambiguous error shape,
    else None. The returned type doubles as the signature-key prefix."""
    m = _EXCEPTION_RE.search(line)
    if m:
        return m.group(1)
    m = _VENDOR_CODE_RE.search(line)
    if m:
        token = m.group(1).upper()
        if token.startswith("ORA-"):
            # The code number IS the failure identity: ORA-12154 stays
            # ORA_12154 even when the surrounding wording varies.
            return token.replace("-", "_")
        if token.startswith("SQLSTATE"):
            return "SQLSTATE"
        if token.startswith("0X"):
            return "HEX_CODE"
        digits = re.search(r"\d+", token)
        return f"ERRNO_{digits.group(0)}" if digits else "ERRNO"
    m = _HTTP_5XX_RE.search(line)
    if m:
        return f"HTTP_{m.group(1)}"
    if _LOG_LEVEL_RE.search(line):
        return "LOG_ERROR"
    return None


def signature_key_for_error(error_type: str, normalized: str) -> str:
    """Stable upper-snake key like ``SSLHANDSHAKEEXCEPTION_HANDSHAKE_FAILED_NO_CIPHER``
    (mirrors the ``SMTP_TIMEOUT_AFTER_OUTPUT_GENERATED`` style the model
    documents). Built from the error type plus the first salient message
    tokens; placeholders and stopwords carry no identity."""
    type_token = error_type.lower()
    tokens = [
        t
        for t in _TOKEN_RE.findall(normalized.lower())
        # Placeholders, stopwords, and bare numbers (date fragments,
        # step counters) carry no failure identity. Neither does the
        # error type repeated inside the message — the key already
        # starts with it ("java.lang.NullPointerException: NullPointer…"
        # must not become NULLPOINTEREXCEPTION_NULLPOINTEREXCEPTION_…).
        if t not in _STOPWORDS
        and not t.startswith("<")
        and len(t) > 1
        and not t.isdigit()
        and t != type_token
        and not t.endswith("." + type_token)
    ]
    key = "_".join([error_type.upper(), *[t.upper() for t in tokens[:6]]])
    return re.sub(r"[^A-Z0-9_]", "_", key)[:120].strip("_")


def extract_error_fingerprints(text: str) -> list[dict[str, str]]:
    """Scan text for error-shaped lines; return unique fingerprints
    (ordered by first occurrence, capped). Pure function — the unit tests'
    surface."""
    seen: dict[str, dict[str, str]] = {}
    for line in text[:MAX_SCAN_CHARS].splitlines()[:MAX_SCAN_LINES]:
        stripped = line.strip()
        if len(stripped) < 12:
            continue
        error_type = _classify_line(stripped)
        if error_type is None:
            continue
        normalized = normalize_error_line(stripped)
        key = signature_key_for_error(error_type, normalized)
        if not key or key in seen:
            continue
        seen[key] = {
            "signature_key": key,
            "error_type": error_type[:80],
            "normalized_message": normalized,
            "example": stripped[:EXAMPLE_CHARS],
        }
        if len(seen) >= MAX_SIGNATURES_PER_EVIDENCE:
            break
    return list(seen.values())


async def fingerprint_evidence(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    evidence: object,
) -> dict:
    """Fingerprint one evidence item and link it to its signatures.

    Find-or-create per (tenant_id, signature_key) with the savepoint /
    IntegrityError pattern used by issue signatures; each hit gains an
    ``evidence -exhibits-> error_signature`` graph edge. Fail-soft by
    design: callers wrap this in try/except and a fingerprinting failure
    must never break normalize.
    """
    counts = {"signatures": 0, "created": 0, "edges": 0}
    text = "\n".join(
        part
        for part in [
            getattr(evidence, "title", None),
            getattr(evidence, "body_text", None),
        ]
        if part
    )
    if not text.strip():
        return counts
    fingerprints = extract_error_fingerprints(text)
    if not fingerprints:
        return counts

    from contextedge.graph.builder import ensure_edge

    for fp in fingerprints:
        signature = (
            await db.execute(
                select(ErrorSignature).where(
                    ErrorSignature.tenant_id == tenant_id,
                    ErrorSignature.signature_key == fp["signature_key"],
                )
            )
        ).scalar_one_or_none()
        if signature is None:
            signature = ErrorSignature(
                tenant_id=tenant_id,
                domain_id=getattr(evidence, "domain_id", None),
                signature_key=fp["signature_key"],
                display_name=fp["example"][:DISPLAY_NAME_CHARS],
                error_type=fp["error_type"],
                normalized_message=fp["normalized_message"],
                patterns=[fp["normalized_message"]],
                example_messages=[fp["example"]],
            )
            try:
                async with db.begin_nested():
                    db.add(signature)
                    await db.flush()
                counts["created"] += 1
            except IntegrityError:
                signature = (
                    await db.execute(
                        select(ErrorSignature).where(
                            ErrorSignature.tenant_id == tenant_id,
                            ErrorSignature.signature_key == fp["signature_key"],
                        )
                    )
                ).scalar_one()
        else:
            examples = list(signature.example_messages or [])
            if (
                fp["example"] not in examples
                and len(examples) < MAX_EXAMPLES_PER_SIGNATURE
            ):
                signature.example_messages = [*examples, fp["example"]]

        await ensure_edge(
            db,
            tenant_id,
            source_type="evidence",
            source_id=evidence.id,
            target_type="error_signature",
            target_id=signature.id,
            edge_type="exhibits",
            weight=1.0,
            # The regex match is deterministic but the *diagnostic* link
            # ("this evidence is about this failure") is slightly weaker —
            # a pasted log excerpt can exhibit a bystander error.
            confidence=0.9,
            domain_id=getattr(evidence, "domain_id", None),
        )
        counts["edges"] += 1
    counts["signatures"] = len(fingerprints)
    return counts
