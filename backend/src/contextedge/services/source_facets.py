"""Structured facets the source system already knows.

A resolved Zoho ticket arrives carrying, as custom fields, the things this
pipeline otherwise pays a model to infer from prose:

    cf_rca                        8 distinct values   "Access Permissions"
    cf_environment_information    6 distinct values   "T3"
    cf_automation_egde_version_1  58 distinct values  "8.2.3"
    cf_enhancement                component           "AE Server"
    cf_list_of_clients_1          customer            "Automation Edge"

Measured on this tenant: populated on **84%** of resolved tickets, and none
of it reaches the evidence body — the values sit in the raw payload where
nothing reads them. Meanwhile `knowledge_applicability` extracts environment
and version from article text at ~7,200 tokens a call, and `issue_signature`
infers a failure taxonomy that `cf_rca` already states in eight words.

The asymmetry is the point: a human who fixed the ticket labelled it. That
label is better evidence than an inference drawn from the same ticket's
prose, and it is free.

**Config-driven, not Zoho-specific.** The mapping lives in the source's own
config (`facet_fields`), because every deployment names its custom fields
differently and a table in this file would be one customer's schema wearing
a general name. A source with no mapping produces no facets and nothing
changes for it.

Facets are recorded, never inferred: a value the source did not set is
absent, and an empty facet map is the normal state for most sources.
"""

from __future__ import annotations

from typing import Any

# What a facet may be used for downstream. Keys are ours; the values a
# deployment maps onto them are theirs.
FACET_KEYS = (
    "root_cause",     # human-assigned cause taxonomy (cf_rca)
    "component",      # affected component / product area
    "environment",    # T3, production, ...
    "version",        # product version the incident was on
    "customer",       # tenant / client the incident belongs to
    "region",
    "ticket_type",
    "knowledge_category",
    "knowledge_tags",
    "knowledge_status",
    "knowledge_url",
    "knowledge_locale",
    "knowledge_updated_at",
    "knowledge_reviewed_at",
)

# Payload sections searched for a mapped field, in order. Zoho nests custom
# fields under `cf`; other connectors put them at the top level.
_SECTIONS = ("cf", "custom_fields", None)

_MAX_VALUE_CHARS = 120
# Ticket custom fields are short labels. KB URLs and joined tag lists are
# not — Zoho portal URLs on this corpus run past 200 characters, and a
# 120-char cut would drop the article slug on re-ingest.
_MAX_URL_CHARS = 2048
_MAX_TAGS_CHARS = 500


def _clean_facet_value(raw: Any, *, max_chars: int = _MAX_VALUE_CHARS) -> str | None:
    if raw is None:
        return None
    value = str(raw).strip()
    # "NA" and "None" are how a form records that nobody filled it in.
    # Storing them would turn an unanswered question into a fact.
    if not value or value.lower() in {"na", "n/a", "none", "null", "-"}:
        return None
    return value[:max_chars]


def _lookup(payload: dict, field: str) -> Any:
    for section in _SECTIONS:
        holder = payload if section is None else payload.get(section)
        if isinstance(holder, dict) and field in holder:
            return holder[field]
    return None


def derive_facets(payload: dict | None, facet_fields: dict | None) -> dict[str, str]:
    """``{facet_key: value}`` for the fields this source maps.

    Pure: payload in, dict out. An unmapped source returns ``{}``, which is
    what most sources will always return — facets are an opportunity where a
    system happens to be well-curated, not a requirement.
    """
    if not payload or not isinstance(facet_fields, dict):
        return {}
    facets: dict[str, str] = {}
    for key, field in facet_fields.items():
        if key not in FACET_KEYS or not isinstance(field, str):
            continue
        value = _clean_facet_value(_lookup(payload, field))
        if value is None:
            continue
        facets[key] = value
    return facets


def derive_knowledge_facets(payload: dict | None) -> dict[str, str]:
    """Source metadata for KB/SOP evidence.

    Article categories, tags and URLs are not incident facts, but they are
    strong retrieval hints for product-specific playbook generation. Zoho Desk
    provides them on every article payload, so record them in ``source_facets``
    instead of leaving them buried in raw JSON where ranking cannot use them.
    """
    if not payload or not isinstance(payload, dict):
        return {}
    if payload.get("record_kind") not in {"kb_article", "sop", "documentation"}:
        return {}

    facets: dict[str, str] = {}
    simple_fields = {
        "knowledge_category": "category_name",
        "knowledge_status": "status",
        "knowledge_url": "web_url",
        "knowledge_locale": "locale",
        "knowledge_updated_at": "updated_at",
        "knowledge_reviewed_at": "reviewed_at",
    }
    for facet_key, payload_key in simple_fields.items():
        max_chars = _MAX_URL_CHARS if facet_key == "knowledge_url" else _MAX_VALUE_CHARS
        value = _clean_facet_value(payload.get(payload_key), max_chars=max_chars)
        if value is None:
            continue
        facets[facet_key] = value

    tags = payload.get("tags")
    if isinstance(tags, list):
        clean_tags = [
            str(tag).strip()
            for tag in tags
            if str(tag).strip()
            and str(tag).strip().lower() not in {"na", "n/a", "none", "null", "-"}
        ]
        if clean_tags:
            facets["knowledge_tags"] = ", ".join(clean_tags)[:_MAX_TAGS_CHARS]

    return facets


# Human-facing case numbers connectors actually emit. ``key`` is omitted:
# Jira issue keys are ticket numbers, but so are object keys on unrelated
# records, and a false ticket_number on a KB article would poison version
# inheritance.
_CASE_NUMBER_KEYS = (
    "ticket_number",
    "ticketNumber",
    "incident_number",
    "caseNumber",
    "case_number",
    "display_id",
    "record_number",
    "number",
)


def _case_number_facet(payload: dict | None) -> dict[str, str]:
    """The ticket number related evidence hangs off.

    Mail-thread rows have no version field of their own. Playbook matching
    finds the parent ticket by this number and copies its version onto
    those rows. Knowledge articles are skipped: they version independently.
    """
    if not isinstance(payload, dict):
        return {}
    if payload.get("record_kind") in {"kb_article", "sop", "documentation"}:
        return {}
    for key in _CASE_NUMBER_KEYS:
        value = payload.get(key)
        if value in (None, ""):
            continue
        number = str(value).strip()[:64]
        if number:
            return {"ticket_number": number}
    return {}


def derive_all_facets(payload: dict | None, facet_fields: dict | None) -> dict[str, str]:
    """Configured ticket facets plus built-in knowledge metadata facets."""
    facets = derive_facets(payload, facet_fields)
    identity = _case_number_facet(payload)
    if identity:
        # Ticket number first so a mapped `version` still wins if both exist.
        facets = {**identity, **facets}
    knowledge_facets = derive_knowledge_facets(payload)
    if knowledge_facets:
        facets = {**facets, **knowledge_facets}
    return facets


def applicability_from_facets(facets: dict[str, str] | None) -> dict[str, Any]:
    """Facets in the shape `knowledge_applicability` already speaks.

    Environment and version are exactly what that service extracts from text
    with a model. When the source states them, the statement wins: a field a
    human filled in beats a value inferred from the same record's prose.
    """
    if not facets:
        return {}
    out: dict[str, Any] = {}
    if facets.get("environment"):
        out["environments"] = [facets["environment"]]
    if facets.get("version"):
        out["versions"] = [facets["version"]]
    if facets.get("component"):
        out["component"] = facets["component"]
    if out:
        # Provenance, so a reviewer can tell a stated fact from an inference
        # — and so a later model-derived value does not silently overwrite
        # one a human typed.
        out["source"] = "source_facets"
    return out
