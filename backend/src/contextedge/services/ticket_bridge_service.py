"""Ticket-number bridging: quoted numbers become case MEMBERSHIP.

P1 of the correlation review, built on the conceptual correction it
insisted on: a ticket number in an email subject proves the email
relates to that ticket's case — it never proves that every ticket
mentioned together is one case. So this module attaches evidence to
cases individually and NEVER unions canonical cases:

- Ticket sources register their human-readable number as an
  authoritative ``CaseIdentifier`` when they correlate, and their own
  evidence gets a ``primary_case`` membership. Registration also
  reconciles pending mentions — an email quoting INC0010427 before the
  incident was ingested links up the moment the ticket arrives
  (ingestion-order independence).
- Unstructured sources (teams / gmail / transcripts) extract
  ticket-shaped tokens from title + body, then **resolve-then-link**:
  only tokens matching a registered identifier become memberships;
  unknown tokens are stored as pending mentions, never as junk keys.
- **Multi-ticket digest guard**: a message resolving to
  ``DIGEST_THRESHOLD`` or more distinct cases is a report, not an
  incident conversation — every membership downgrades to
  ``mentioned_only``, which the episode cluster resolver never expands
  through.

Confidence by extraction location (the review's table): subject/title
0.98, body 0.9 — structured primary memberships are 1.0.
"""

from __future__ import annotations

import re
import uuid

import structlog
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.models.case_bridge import (
    CaseIdentifier,
    EvidenceCaseMembership,
    PendingIdentifierMention,
)
from contextedge.models.evidence import EvidenceItem

logger = structlog.get_logger()

# Ticket-shaped tokens: ServiceNow-style prefixed numbers (INC0010427,
# PRB0004031, CHG0003321, RITM0012345) and Jira-style keys (ITOPS-101).
# Deliberately conservative — a missed mention is a pending gap, a false
# hit is noise in the membership table.
_TICKET_TOKEN_RE = re.compile(
    r"\b(?:(?:INC|PRB|CHG|RITM|REQ|TASK|CS)\d{6,9}|[A-Z][A-Z0-9]{1,9}-\d{1,10})\b"
)

MAX_TOKENS_PER_EVIDENCE = 20
DIGEST_THRESHOLD = 3
SUBJECT_CONFIDENCE = 0.98
BODY_CONFIDENCE = 0.9

# Sources whose evidence is a ticket record (membership = primary_case,
# identifier registration) vs conversational sources that quote numbers.
TICKET_SOURCE_TYPES = {"servicenow", "jira_sm", "sapphireims"}
CONVERSATIONAL_SOURCE_TYPES = {"teams", "gmail", "local_file"}


def extract_ticket_tokens(text: str | None, cap: int = MAX_TOKENS_PER_EVIDENCE) -> list[str]:
    if not text:
        return []
    seen: list[str] = []
    for match in _TICKET_TOKEN_RE.finditer(text):
        token = match.group(0).upper()
        if token not in seen:
            seen.append(token)
            if len(seen) >= cap:
                break
    return seen


def ticket_display_number(source_type: str, payload: dict | None) -> str | None:
    """The human-readable number of a ticket payload, per source shape."""
    p = payload or {}
    if source_type == "servicenow":
        value = p.get("number")
    elif source_type == "jira_sm":
        value = p.get("key")
    elif source_type == "sapphireims":
        value = p.get("ticket_id")
    else:
        value = None
    if isinstance(value, str) and value.strip():
        return value.strip().upper()
    return None


async def _add_membership(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    evidence_id: uuid.UUID,
    canonical_case_id: uuid.UUID,
    relationship_type: str,
    confidence: float,
    extraction_location: str | None,
) -> bool:
    """Idempotent membership insert; existing rows are left untouched
    (first-writer wins — a primary_case row must not be downgraded by a
    later mention of the same case)."""
    existing = (
        await db.execute(
            select(EvidenceCaseMembership.id).where(
                EvidenceCaseMembership.evidence_id == evidence_id,
                EvidenceCaseMembership.canonical_case_id == canonical_case_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return False
    try:
        async with db.begin_nested():
            db.add(
                EvidenceCaseMembership(
                    tenant_id=tenant_id,
                    evidence_id=evidence_id,
                    canonical_case_id=canonical_case_id,
                    relationship_type=relationship_type,
                    confidence=confidence,
                    extraction_location=extraction_location,
                )
            )
            await db.flush()
        return True
    except IntegrityError:
        return False  # concurrent writer won the unique race


async def register_ticket_identifier(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    evidence: EvidenceItem,
    source_type: str,
    payload: dict | None,
    canonical_case_id: uuid.UUID | None,
) -> dict:
    """Called when TICKET evidence correlates: register the number as an
    authoritative identifier, give the ticket its primary membership,
    and reconcile pending mentions waiting on this number."""
    counts = {"registered": False, "primary_membership": False, "reconciled_mentions": 0}
    if canonical_case_id is None:
        return counts
    number = ticket_display_number(source_type, payload)
    if number is None:
        return counts

    existing = (
        await db.execute(
            select(CaseIdentifier).where(
                CaseIdentifier.tenant_id == tenant_id,
                CaseIdentifier.source_system == source_type,
                CaseIdentifier.normalized_value == number,
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        try:
            async with db.begin_nested():
                db.add(
                    CaseIdentifier(
                        tenant_id=tenant_id,
                        canonical_case_id=canonical_case_id,
                        source_system=source_type,
                        normalized_value=number,
                        display_value=number,
                    )
                )
                await db.flush()
            counts["registered"] = True
        except IntegrityError:
            pass  # concurrent registration
    elif existing.canonical_case_id != canonical_case_id:
        # The same number pointing at a different canonical case is a
        # data-quality signal (case re-anchoring) — log, never clobber.
        logger.warning(
            "ticket_bridge.identifier_case_mismatch",
            tenant_id=str(tenant_id),
            number=number,
            registered_case=str(existing.canonical_case_id),
            new_case=str(canonical_case_id),
        )

    counts["primary_membership"] = await _add_membership(
        db, tenant_id, evidence.id, canonical_case_id, "primary_case", 1.0, "structured"
    )

    # Ingestion-order independence: mentions that arrived before this
    # ticket now resolve to it.
    pending = (
        await db.execute(
            select(PendingIdentifierMention).where(
                PendingIdentifierMention.tenant_id == tenant_id,
                PendingIdentifierMention.normalized_value == number,
                PendingIdentifierMention.status == "pending",
            ).limit(100)
        )
    ).scalars().all()
    for mention in pending:
        added = await _add_membership(
            db,
            tenant_id,
            mention.evidence_id,
            canonical_case_id,
            "explicit_reference",
            BODY_CONFIDENCE if mention.extraction_location != "subject" else SUBJECT_CONFIDENCE,
            mention.extraction_location,
        )
        mention.status = "resolved"
        mention.resolved_case_id = canonical_case_id
        if added:
            counts["reconciled_mentions"] += 1
    return counts


async def bridge_conversational_mentions(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    evidence: EvidenceItem,
) -> dict:
    """Called when CONVERSATIONAL evidence correlates: extract quoted
    ticket numbers, resolve against registered identifiers, attach
    memberships (or store pending mentions). Never unions cases."""
    counts = {"memberships": 0, "pending": 0, "digest_downgraded": False}

    subject_tokens = extract_ticket_tokens(evidence.title)
    body_tokens = [
        t for t in extract_ticket_tokens(evidence.body_text) if t not in subject_tokens
    ]
    if not subject_tokens and not body_tokens:
        return counts

    located = [("subject", t) for t in subject_tokens] + [("body", t) for t in body_tokens]

    resolved: list[tuple[str, str, uuid.UUID]] = []
    unresolved: list[tuple[str, str]] = []
    for location, token in located:
        case_ids = (
            await db.execute(
                select(CaseIdentifier.canonical_case_id).where(
                    CaseIdentifier.tenant_id == tenant_id,
                    CaseIdentifier.normalized_value == token,
                    CaseIdentifier.is_authoritative.is_(True),
                ).limit(3)
            )
        ).scalars().all()
        distinct = set(case_ids)
        if len(distinct) == 1:
            resolved.append((location, token, next(iter(distinct))))
        elif len(distinct) > 1:
            # The same value registered by two systems (a SapphireIMS
            # "INC-4021" also matches the Jira key shape). Ambiguity
            # abstains — the review's rule: multiple matches → review,
            # never an arbitrary pick.
            counts["ambiguous"] = counts.get("ambiguous", 0) + 1
            logger.info(
                "ticket_bridge.ambiguous_identifier",
                tenant_id=str(tenant_id),
                token=token,
                case_count=len(distinct),
            )
        else:
            unresolved.append((location, token))

    # Multi-ticket digest guard: many distinct cases in one message is a
    # status report, not an incident conversation.
    distinct_cases = {case_id for _loc, _tok, case_id in resolved}
    is_digest = len(distinct_cases) >= DIGEST_THRESHOLD
    counts["digest_downgraded"] = is_digest

    for location, _token, case_id in resolved:
        relationship = "mentioned_only" if is_digest else "explicit_reference"
        confidence = (
            0.5 if is_digest
            else (SUBJECT_CONFIDENCE if location == "subject" else BODY_CONFIDENCE)
        )
        if await _add_membership(
            db, tenant_id, evidence.id, case_id, relationship, confidence, location
        ):
            counts["memberships"] += 1

    for location, token in unresolved:
        existing = (
            await db.execute(
                select(PendingIdentifierMention.id).where(
                    PendingIdentifierMention.evidence_id == evidence.id,
                    PendingIdentifierMention.normalized_value == token,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            continue
        try:
            async with db.begin_nested():
                db.add(
                    PendingIdentifierMention(
                        tenant_id=tenant_id,
                        evidence_id=evidence.id,
                        normalized_value=token,
                        extraction_location=location,
                    )
                )
                await db.flush()
            counts["pending"] += 1
        except IntegrityError:
            continue
    return counts
