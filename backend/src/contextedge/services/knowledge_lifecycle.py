"""Whether the source system considers an article current.

ServiceNow is the system of record for knowledge: articles are drafted,
reviewed, published and retired there, and that lifecycle is the customer's
governance, not ours. The connector has always fetched `workflow_state` on
`kb_knowledge` — and until this module, that field appeared exactly once in
the entire codebase, in the list of fields to fetch. It was read and thrown
away.

So a draft nobody approved, an article sitting in review, and one a human
explicitly retired were all served to the agent exactly like a published
one — with a citation that makes them look checked. An engineer acting on a
retired article is the failure this closes.

Three rules:

- **Absent state serves.** Most knowledge has no lifecycle at all (a SOP on
  a file share, an uploaded PDF), and a NULL here means "this source does not
  say", never "withheld". Treating unknown as not-current would empty the
  corpus for every source but one.
- **Withheld means withheld, not demoted.** F4b demotes a superseded article
  rather than dropping it, because a filename heuristic guessed. Here a human
  used their own system to say this is not current guidance; serving it
  anyway — even ranked last — overrides that decision.
- **One rule, one home.** Retrieval filters in Python over semantic hits and
  the agent projection filters in SQL. Both import from here so they cannot
  drift into disagreeing about what "current" means.

Adding a source: map its lifecycle field and vocabulary below. Verify the
values against a live instance first — an invented status that never matches
silently withholds nothing, and an over-broad one silently withholds
everything. See also `services/evidence_typing.py`, which derives
`evidence_type` from the same payloads.
"""

from __future__ import annotations

from typing import Any

# Normalized vocabulary. `published` is the only state that means "current";
# the rest are recorded rather than collapsed, because "still in review" and
# "withdrawn" are different answers to a reviewer asking why an article did
# not appear.
KNOWLEDGE_STATES = ("draft", "review", "published", "retired")

# States that must not reach an agent or a responder as guidance.
WITHHELD_KNOWLEDGE_STATES = ("draft", "review", "retired")

# (source_type, payload field) -> the field holding the lifecycle state.
_LIFECYCLE_FIELDS: dict[str, str] = {
    # Verified against the connector's own field list for `kb_knowledge`.
    "servicenow": "workflow_state",
}

# (source_type, raw value) -> normalized state. Raw values are lower-cased
# before lookup. An unmapped value yields None — unknown, therefore served,
# because withholding on a value we do not understand is the more damaging
# of the two mistakes.
_STATE_MAP: dict[tuple[str, str], str] = {
    ("servicenow", "draft"): "draft",
    ("servicenow", "review"): "review",
    ("servicenow", "published"): "published",
    ("servicenow", "retired"): "retired",
    # ServiceNow instances that use the pending-retirement step still mean
    # "on its way out" — but it is published until it is not, so it serves.
    ("servicenow", "pending_retirement"): "published",
}


def derive_knowledge_state(payload: dict | None) -> str | None:
    """The source's lifecycle state for this record, normalized, or None.

    Pure and payload-only, so it is testable without a database and reusable
    by a backfill over stored payloads.
    """
    p = payload or {}
    source_type = str(p.get("_connector_source_type") or "").strip().lower()
    if not source_type:
        return None
    field = _LIFECYCLE_FIELDS.get(source_type)
    if not field:
        return None
    raw = p.get(field)
    if raw is None:
        return None
    return _STATE_MAP.get((source_type, str(raw).strip().lower()))


def is_current(state: str | None) -> bool:
    """True when this article may be served as guidance.

    None is current: the source did not say, and most knowledge has no
    lifecycle to say it with.
    """
    return state not in WITHHELD_KNOWLEDGE_STATES


def current_knowledge_clause(model: Any):
    """SQL form of `is_current`, for queries that filter in the database.

    Written as an explicit NULL branch rather than `notin_`, because SQL's
    three-valued logic drops NULL rows from a `NOT IN` — which would withhold
    exactly the articles whose source never had a lifecycle.
    """
    from sqlalchemy import or_

    column = model.knowledge_state
    return or_(column.is_(None), column.notin_(WITHHELD_KNOWLEDGE_STATES))
