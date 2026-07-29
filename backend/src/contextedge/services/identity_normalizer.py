"""Normalization of extracted entities into typed identity signals.

The extractor (``ai/extractors/identity_extractor.py``, prompt ``identity``
v2) returns a display name plus optional structured identifiers. This
module turns that raw dict into a ``NormalizedEntity`` whose identifiers
are typed and canonicalized so the resolver can match deterministically:

- emails / usernames / hostnames / FQDNs casefolded and trimmed
- a display name that *looks like* an email or hostname is reclassified as
  that strong identifier (extractors frequently put ``jsmith@acme.com``
  in ``name``)
- ``source_identifiers`` (``{"teams_user_id": "29:abc"}``) become
  ``external_id`` aliases tagged with their source system
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_FQDN_RE = re.compile(r"^(?=.{4,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.){2,}[a-z]{2,}$")
_HOSTNAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_IPV4_RE = re.compile(r"^(?:\d{1,3}\.){3}\d{1,3}$")


def normalize_text(value: str) -> str:
    # .lower(), not .casefold(): must produce byte-identical output to the
    # 0033 SQL backfill (PostgreSQL lower()), or backfilled aliases never
    # match runtime lookups.
    return " ".join(str(value).strip().split()).lower()


@dataclass(slots=True)
class NormalizedEntity:
    entity_type: str
    display_name: str
    normalized_name: str
    context: str | None = None
    # alias_type -> list of (normalized_value, source_system | None)
    identifiers: dict[str, list[tuple[str, str | None]]] = field(default_factory=dict)

    def add_identifier(self, alias_type: str, value: str, source_system: str | None = None) -> None:
        normalized = normalize_text(value)
        if not normalized:
            return
        bucket = self.identifiers.setdefault(alias_type, [])
        if all(existing != normalized for existing, _ in bucket):
            bucket.append((normalized, source_system))

    @property
    def strong_identifiers(self) -> list[tuple[str, str, str | None]]:
        """Flat list of (alias_type, normalized_value, source_system)."""
        return [
            (alias_type, value, source_system)
            for alias_type, bucket in self.identifiers.items()
            for value, source_system in bucket
        ]


def _classify_bare_name(name: str) -> str | None:
    """Detect a display name that is really a strong identifier."""
    lowered = name.lower().strip()
    if _EMAIL_RE.match(lowered):
        return "email"
    if _IPV4_RE.match(lowered):
        return "ip_address"
    if _FQDN_RE.match(lowered):
        return "fqdn"
    return None


def normalize_extracted_entity(entity: dict) -> NormalizedEntity | None:
    name = str(
        entity.get("display_name") or entity.get("name") or ""
    ).strip()
    if not name:
        return None
    # Lowercase the type so an extractor emitting "Person" doesn't fork a
    # parallel namespace from "person" (all match layers are type-scoped)
    # or dodge the stricter person auto-link threshold.
    entity_type = str(entity.get("entity_type") or "unknown").strip().lower() or "unknown"
    context = entity.get("context")

    normalized = NormalizedEntity(
        entity_type=entity_type,
        display_name=name,
        normalized_name=normalize_text(name),
        context=str(context) if context else None,
    )

    for key, alias_type in (
        ("email", "email"),
        ("username", "username"),
        ("hostname", "hostname"),
        ("fqdn", "fqdn"),
        ("serial_number", "serial_number"),
    ):
        value = entity.get(key)
        if value:
            normalized.add_identifier(alias_type, str(value))

    for value in entity.get("ip_addresses") or []:
        if value:
            normalized.add_identifier("ip_address", str(value))

    source_identifiers = entity.get("source_identifiers")
    if isinstance(source_identifiers, dict):
        for system, value in source_identifiers.items():
            if value:
                normalized.add_identifier("external_id", str(value), str(system))

    # A "name" that is actually an email/FQDN/IP is a strong signal, not a
    # display name — classify it so Layer 1 can use it.
    bare_type = _classify_bare_name(name)
    if bare_type is not None:
        normalized.add_identifier(bare_type, name)
    elif entity_type == "device" and _HOSTNAME_RE.match(normalized.normalized_name):
        # Single-token device names like "vpn-gw-east-01" are hostnames.
        normalized.add_identifier("hostname", name)

    return normalized
