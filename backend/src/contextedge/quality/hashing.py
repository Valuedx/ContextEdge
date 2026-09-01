"""Canonical hashing for quality-bearing content.

RFC 8785 (JCS) via ``rfc8785``, the same dependency the approval/artifact
binding already uses — deliberately not a hand-rolled ``json.dumps(sort_keys=True)``.
The parts a naive dump gets wrong are ECMAScript number serialisation and
UTF-16 key ordering, and a hash that is only right for the values we happened
to test is worse than no hash: it silently declares two different playbooks
identical.

Everything hashed here has to survive a round trip through JSONB, so the
normaliser coerces UUIDs, datetimes and Decimals to strings before
canonicalisation rather than letting the encoder guess.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import rfc8785

# Keys excluded from the content hash. These change on every save without
# changing what a reviewer or an operator would judge, and including them
# would invalidate a perfectly good assessment on a no-op write.
VOLATILE_KEYS: frozenset[str] = frozenset(
    {
        "updated_at",
        "created_at",
        "last_edited_by",
        "revision",
        "edited_at",
        "edited_by",
        # Counters the generator stamps for observability, not content.
        "citation_validation",
        "branching_validation",
    }
)


def normalize(value: Any, *, _depth: int = 0) -> Any:
    """Coerce a value into something RFC 8785 can canonicalise.

    Depth-limited: content comes partly from model output, and a pathological
    nesting depth should produce a truncation marker rather than a
    RecursionError inside a hash function that everything else depends on.
    """
    if _depth > 32:
        return "<max-depth>"
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        # NaN/Inf are not representable in JSON and rfc8785 rejects them.
        # A model-authored confidence has been seen as NaN.
        if value != value or value in (float("inf"), float("-inf")):
            return None
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (uuid.UUID, datetime, date)):
        return str(value)
    if isinstance(value, dict):
        return {
            str(key): normalize(item, _depth=_depth + 1)
            for key, item in value.items()
            if str(key) not in VOLATILE_KEYS
        }
    if isinstance(value, (list, tuple)):
        return [normalize(item, _depth=_depth + 1) for item in value]
    return str(value)


def canonical_bytes(payload: Any) -> bytes:
    """RFC 8785 canonical JSON for ``payload``."""
    return rfc8785.dumps(normalize(payload))


def content_hash(payload: Any) -> str:
    """Stable sha256 hex digest of ``payload``.

    Two structurally identical payloads hash identically regardless of key
    insertion order — which is what makes "did this content actually change?"
    answerable, and therefore what makes a title-only edit invalidate the
    assessment while a re-save of unchanged text does not.
    """
    return hashlib.sha256(canonical_bytes(payload)).hexdigest()


def combine_hashes(*parts: str | None) -> str:
    """Hash of an ordered list of hashes, for composite dependency keys.

    ``None`` is encoded distinctly from the empty string: "no policy pack" and
    "a policy pack whose version is blank" must not collide.
    """
    joined = "\x1f".join("\x00" if part is None else part for part in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()
