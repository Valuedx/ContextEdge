"""What the source system says about a ticket's state.

The resolution gate decides whether a cluster is worth synthesising into an
episode, and episode synthesis is the costliest lane in the pipeline —
2.26M tokens on this tenant, ~29% of everything spent, with **71% of the
episodes it produced later superseded**.

The gate's own docstring lists "structural: closed/resolved status
vocabulary" as its first tier. It was never structural. It read the ticket's
TEXT with a regex — including a literal ``resolved by agent`` alternation,
which is Zoho's *status value* being matched as prose. So a ticket Zoho marked
``Closed`` was deferred unless somebody happened to type a resolution phrase
into it, and a ticket that merely discussed a fix could pass.

This reads the field instead. Two states, deliberately separate:

- **resolved** — the source says a fix landed (`Closed`, `Resolved By Agent`,
  `Resolved By Plugin Team`; ServiceNow 6/7). There is something to learn, so
  the gate opens.
- **cancelled** — terminal, but nothing was fixed (`Cancelled`; ServiceNow 8).
  The case is over and there is no resolution knowledge in it. Synthesising
  one spends the money the gate exists to save, so terminal is NOT the same
  question as resolved and this module refuses to collapse them.

Everything else — open, in progress, awaiting a customer — is `None`: still
running, nothing asserted. As with `knowledge_lifecycle`, an unmapped value
means "the source did not say", never "no".

Vocabularies are keyed on ``(source_type, object_type)`` and were read off
this tenant's own payloads, not from documentation.
"""

from __future__ import annotations

from typing import Any

import structlog

logger = structlog.get_logger()

# Normalized vocabulary. Only these two are asserted; anything else is silence.
CASE_STATES = ("resolved", "cancelled")

# (source_type, object_type) -> payload field holding the state.
_STATE_FIELDS: dict[tuple[str, str], str] = {
    ("zoho_desk", "tickets"): "status",
    ("servicenow", "incident"): "state",
    ("servicenow", "problem"): "state",
    ("servicenow", "sc_req_item"): "state",
    ("servicenow", "sc_task"): "state",
}

# (source_type, raw value lowercased) -> normalized state.
#
# Zoho values are the ones this tenant actually emits. `Resolved By Plugin
# Team` is included alongside `Resolved By Agent` because it means the same
# thing to a knowledge pipeline — the ticket was fixed, by a different team.
# A deployment with its own custom "Resolved By ..." statuses gets them via
# the prefix rule below rather than needing an entry each.
_STATE_MAP: dict[tuple[str, str], str] = {
    ("zoho_desk", "closed"): "resolved",
    # A plain `Resolved` exists alongside the `Resolved By ...` family — 340
    # tickets on this tenant, which the prefix rule below does NOT catch.
    ("zoho_desk", "resolved"): "resolved",
    ("zoho_desk", "resolved by agent"): "resolved",
    ("zoho_desk", "resolved by plugin team"): "resolved",
    ("zoho_desk", "cancelled"): "cancelled",
    ("zoho_desk", "canceled"): "cancelled",
    # ServiceNow ships numeric states: 6 Resolved, 7 Closed, 8 Canceled.
    ("servicenow", "6"): "resolved",
    ("servicenow", "7"): "resolved",
    ("servicenow", "8"): "cancelled",
    # Some instances expose the label rather than the number.
    ("servicenow", "resolved"): "resolved",
    ("servicenow", "closed"): "resolved",
    ("servicenow", "canceled"): "cancelled",
    ("servicenow", "cancelled"): "cancelled",
}

# Zoho lets an admin name their own resolution statuses, and every deployment
# does. Matching the prefix keeps a tenant's "Resolved By Network Team" from
# being invisible — the phrasing is Zoho's own convention, and the alternative
# is a config entry per customer per team.
_RESOLVED_PREFIXES = {"zoho_desk": ("resolved by", "resolved -")}

_REPORTED_UNMAPPED: set[tuple[str, str]] = set()


def derive_case_state(payload: dict | None) -> str | None:
    """``"resolved"``, ``"cancelled"``, or None for anything still running.

    Pure and payload-only, so the vocabulary can be tested without a database
    and re-derived over stored payloads.
    """
    p = payload or {}
    source_type = str(p.get("_connector_source_type") or "").strip().lower()
    object_type = str(p.get("_connector_object_type") or "").strip().lower()
    field = _STATE_FIELDS.get((source_type, object_type))
    if not field:
        return None
    raw = p.get(field)
    if raw is None or str(raw).strip() == "":
        return None
    value = str(raw).strip().lower()

    state = _STATE_MAP.get((source_type, value))
    if state is not None:
        return state

    for prefix in _RESOLVED_PREFIXES.get(source_type, ()):
        if value.startswith(prefix):
            return "resolved"

    key = (source_type, value)
    if key not in _REPORTED_UNMAPPED:
        # Not an error: "Open" and "Work In Progress" are unmapped on purpose.
        # Logged at debug so a genuinely new terminal status is discoverable
        # without narrating every in-flight ticket.
        _REPORTED_UNMAPPED.add(key)
        logger.debug(
            "case_state.unmapped",
            source_type=source_type,
            object_type=object_type,
            value=value[:60],
        )
    return None


def is_resolved(state: str | None) -> bool:
    """True only when the source says a fix landed.

    `cancelled` is deliberately false: the case is over, and there is nothing
    in it worth paying an LLM to summarise.
    """
    return state == "resolved"


def resolved_clause(model: Any):
    """SQL form, for queries that filter in the database."""
    return model.case_state == "resolved"
