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
)

# Payload sections searched for a mapped field, in order. Zoho nests custom
# fields under `cf`; other connectors put them at the top level.
_SECTIONS = ("cf", "custom_fields", None)

_MAX_VALUE_CHARS = 120


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
        raw = _lookup(payload, field)
        if raw is None:
            continue
        value = str(raw).strip()
        # "NA" and "None" are how a form records that nobody filled it in.
        # Storing them would turn an unanswered question into a fact.
        if not value or value.lower() in {"na", "n/a", "none", "null", "-"}:
            continue
        facets[key] = value[:_MAX_VALUE_CHARS]
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
