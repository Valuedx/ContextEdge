"""Derive ``EvidenceItem.evidence_type`` from what the connector fetched.

Until this module existed, ``evidence_type`` was read straight off the
payload with a ``"message"`` default — and **no connector except
``zoho_desk`` ever set it**. Every record from every other source, a
ServiceNow KB article as much as a Teams chat line, normalized to
``"message"``.

That silently disabled three shipped features rather than one:

- ``memory_service.KB_LONG_TERM_TYPES`` (``kb_article`` / ``sop`` /
  ``documentation``) is a correct set that nothing produced members for,
  so knowledge was never promoted to long-term memory.
- ``evidence_chunk_service._default_authority`` could not tell an
  approved SOP from an incident ticket, so a knowledge article carried
  ``source_authority: "ticket"`` into the reranker.
- ``extraction_tasks.resolve_synthesis_role`` could not give a knowledge
  article document authority, so a general "how the VPN works" page
  competed with the incident record on incident-specific fields.

The fix is deliberately **central rather than per-connector**. Asking six
connectors to remember a convention is how the convention was missed the
first time; the payload already carries ``_connector_object_type``, which
is the ground truth about what was fetched. A connector that stamps
``evidence_type`` itself still wins — this is the floor, not an override.

Add a mapping here when a connector learns a new object type. Unknown
object types fall back to the source's default rather than to
``"message"``, so a new ServiceNow table is at least a ticket.
"""

from __future__ import annotations

DEFAULT_EVIDENCE_TYPE = "message"

# (source_type, connector object_type) -> evidence_type.
#
# Object types are the literal values the connectors emit; see
# ``connectors/*/connector.py``. ServiceNow uses the table name, Zoho the
# module name, the rest a fixed label.
_OBJECT_TYPE_MAP: dict[tuple[str, str], str] = {
    # ServiceNow — table names. kb_knowledge is the one that matters most:
    # it is the KB, and it was landing as "message".
    ("servicenow", "incident"): "incident",
    ("servicenow", "problem"): "problem",
    ("servicenow", "change_request"): "change",
    ("servicenow", "kb_knowledge"): "kb_article",
    ("servicenow", "sc_req_item"): "service_request",
    ("servicenow", "sc_task"): "task",
    ("servicenow", "em_alert"): "alert",
    ("servicenow", "em_alert_rollup"): "alert",
    # Jira Service Management — one object type; the record kind lives in
    # the payload's kind-prefixed thread id, not the object type.
    ("jira_sm", "issue"): "ticket",
    # SapphireIMS
    ("sapphireims", "ticket"): "ticket",
    # Zoho Desk — the connector already stamps evidence_type itself; these
    # entries keep the derivation correct if that is ever removed.
    ("zoho_desk", "tickets"): "ticket",
    ("zoho_desk", "articles"): "kb_article",
    # Conversational sources
    ("teams", "channel_message"): "chat_message",
    ("gmail", "email_thread"): "email",
}

# Fallback when the object type is unrecognized but the source is known.
# A new ServiceNow table should still be a ticket rather than a chat
# message — wrong-but-adjacent beats wrong-and-misleading.
_SOURCE_DEFAULTS: dict[str, str] = {
    "servicenow": "ticket",
    "jira_sm": "ticket",
    "sapphireims": "ticket",
    "zoho_desk": "ticket",
    "teams": "chat_message",
    "gmail": "email",
    "local_file": "document",
}

# Evidence types that represent knowledge rather than events. Kept here,
# next to what produces them, so the memory layer and the authority
# mapping cannot drift from the producer.
KNOWLEDGE_EVIDENCE_TYPES = frozenset({"kb_article", "sop", "documentation"})

# Types an uploader may declare for a batch of files. Uploads are the one
# ingestion path with a human present at the point of ingest, so the
# document kind can simply be *asked* rather than inferred — an SOP PDF
# is indistinguishable from a runbook or a post-mortem by filename alone,
# and getting it wrong costs knowledge authority and long-term memory
# placement.
#
# Constrained to a known set: a free-text evidence_type would let a typo
# ("kb-article") silently miss KNOWLEDGE_EVIDENCE_TYPES, which is the
# exact class of failure this module exists to end.
UPLOADABLE_EVIDENCE_TYPES = frozenset(
    {
        "kb_article",
        "sop",
        "documentation",
        "runbook",
        "postmortem",
        "transcript",
        "document",
        "message",
    }
)


def derive_evidence_type(payload: dict | None) -> str:
    """The evidence type for a raw payload.

    Resolution order:

    1. An explicit ``evidence_type`` on the payload. A connector that
       knows better than this table wins — ``zoho_desk`` distinguishes
       tickets from KB articles inside one source and says so directly.
    2. The ``(source_type, object_type)`` mapping.
    3. The source's default type.
    4. ``"message"``.

    Pure and payload-only, so it is testable without a database and
    reusable by a backfill over ``RawEvidenceObject`` rows.
    """
    p = payload or {}

    explicit = p.get("evidence_type")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()

    source_type = str(p.get("_connector_source_type") or "").strip()
    object_type = str(p.get("_connector_object_type") or "").strip()

    mapped = _OBJECT_TYPE_MAP.get((source_type, object_type))
    if mapped:
        return mapped

    return _SOURCE_DEFAULTS.get(source_type, DEFAULT_EVIDENCE_TYPE)


def is_knowledge_evidence(evidence_type: str | None) -> bool:
    """True for normative knowledge (KB article, SOP, documentation).

    Knowledge is not evidence that an incident occurred — it is what
    *should* be done. Callers use this to keep the two apart: knowledge
    must not be clustered into an episode as if it were a record of
    events.
    """
    return (evidence_type or "") in KNOWLEDGE_EVIDENCE_TYPES


# Payload keys connectors use for the human-facing record number, in
# preference order. Zoho writes `ticket_number`, ServiceNow `number`,
# ManageEngine `display_id` — a reviewer just wants the number printed
# on the ticket, whichever connector produced it.
_DISPLAY_ID_KEYS = (
    "ticket_number",
    "number",
    "display_id",
    "record_number",
    "key",
    "incident_number",
)

# And the deep link back into the source system.
_URL_KEYS = ("web_url", "url", "permalink", "link", "portal_url", "href")


def source_reference_from_payload(
    payload: dict | None, external_id: str | None, source_type: str | None
) -> dict:
    """The record's identity in the system it came from.

    Every field here was already stored on the raw object and none of it
    reached the API, so evidence in the UI could not be traced back to a
    ticket. The internal UUID was the only identifier shown, and it is
    the one identifier nobody can search for or open.

    Falls back to the external id for display: a connector with no
    friendlier number should still show something actionable rather than
    nothing.
    """
    data = payload if isinstance(payload, dict) else {}

    display_id = None
    for key in _DISPLAY_ID_KEYS:
        value = data.get(key)
        if value not in (None, ""):
            display_id = str(value)
            break

    url = None
    for key in _URL_KEYS:
        value = data.get(key)
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            url = value
            break

    return {
        "external_id": external_id,
        "display_id": display_id or external_id,
        "url": url,
        "source_type": source_type,
    }
