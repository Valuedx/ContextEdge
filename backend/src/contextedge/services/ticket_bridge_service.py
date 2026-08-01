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
from contextedge.models.evidence import EvidenceItem, RawEvidenceObject
from contextedge.models.source import Source

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


# --- Quoted / forwarded content (A5) ----------------------------------------

# Deterministic quote markers. Everything from a block marker to the end
# of the text is quoted (forwards and Outlook-style reply blocks embed
# the older message below the marker); ">"-prefixed lines are quoted
# individually.
_QUOTE_BLOCK_MARKERS = (
    "---------- forwarded message ----------",
    "-----original message-----",
    "________________________________",  # Outlook divider
)
_QUOTE_LINE_PREFIX = ">"
_OUTLOOK_FROM_RE = re.compile(r"^from: .+", re.IGNORECASE)
_OUTLOOK_SENT_RE = re.compile(r"^(sent|date): .+", re.IGNORECASE)

# --- Bot messages (A6) ------------------------------------------------------

# A recognized ticket card is the ticket system speaking through the
# bot — near-identifier confidence, parsed structurally, no LLM. The
# bot's own prose is second-hand narration: downweighted, and never a
# thread-topic anchor.
BOT_CARD_CONFIDENCE = 0.95
BOT_TEXT_CONFIDENCE = 0.7
BOT_REPLY_INHERITANCE_CONFIDENCE = 0.6


def is_bot_message(payload: dict | None) -> bool:
    p = payload or {}
    return bool(p.get("is_bot") or p.get("from_application"))


def extract_bot_card_tokens(payload: dict | None) -> list[str]:
    """Ticket-shaped tokens inside card attachments, extracted from the
    structured payload — never from prose. Order-preserving, deduped."""
    import json

    tokens: list[str] = []
    for attachment in (payload or {}).get("attachments") or []:
        if not isinstance(attachment, dict):
            continue
        content = attachment.get("content")
        if isinstance(content, dict | list):
            text = json.dumps(content)
        elif isinstance(content, str):
            text = content
        else:
            continue
        for match in _TICKET_TOKEN_RE.finditer(text):
            if match.group(0) not in tokens:
                tokens.append(match.group(0))
    return tokens


QUOTED_MENTION_CONFIDENCE = 0.55
# A case mentioned only inside quoted content counts half toward the
# digest threshold: a quoted digest is second-hand reporting twice over.
QUOTED_DIGEST_WEIGHT = 0.5


def quoted_ranges(text: str | None) -> list[tuple[int, int]]:
    """Character ranges of quoted/forwarded content. Conservative and
    deterministic: block markers quote everything below them; ">" lines
    quote themselves; a "From:" line followed shortly by "Sent:"/"Date:"
    (Outlook reply header) quotes everything from the "From:" line on."""
    if not text:
        return []
    lower = text.lower()
    ranges: list[tuple[int, int]] = []
    block_start = len(text)
    for marker in _QUOTE_BLOCK_MARKERS:
        idx = lower.find(marker)
        if idx != -1:
            block_start = min(block_start, idx)

    offset = 0
    lines = text.split("\n")
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        line_start = offset
        offset += len(line) + 1
        if line_start >= block_start:
            break
        if stripped.startswith(_QUOTE_LINE_PREFIX):
            ranges.append((line_start, line_start + len(line)))
            continue
        if _OUTLOOK_FROM_RE.match(stripped):
            following = [ln.strip() for ln in lines[i + 1 : i + 4]]
            if any(_OUTLOOK_SENT_RE.match(ln) for ln in following):
                block_start = min(block_start, line_start)
                break
    if block_start < len(text):
        ranges.append((block_start, len(text)))
    return ranges


def _in_ranges(pos: int, ranges: list[tuple[int, int]]) -> bool:
    return any(start <= pos < end for start, end in ranges)


def extract_ticket_tokens_with_spans(text: str | None) -> list[tuple[str, bool]]:
    """(token, is_quoted) pairs, deduped keeping the strongest form: a
    token appearing BOTH in fresh text and a quote counts as fresh."""
    if not text:
        return []
    ranges = quoted_ranges(text)
    best: dict[str, bool] = {}
    for match in _TICKET_TOKEN_RE.finditer(text):
        token = match.group(0)
        is_quoted = _in_ranges(match.start(), ranges)
        if token not in best or (best[token] and not is_quoted):
            best[token] = is_quoted
    return list(best.items())


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
    status: str = "active",
) -> bool:
    """Idempotent membership insert; existing rows are left untouched
    (first-writer wins — a primary_case row must not be downgraded by a
    later mention of the same case, and a negative row permanently
    blocks automatic re-linking of its pair)."""
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
                    status=status,
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
        if mention.extraction_location == "dissociation":
            # The mention was evidence AGAINST the link (A7): the ticket
            # arriving later reconciles it to a negative row, exactly as
            # it would have been written had the ticket existed first.
            added = await _add_membership(
                db,
                tenant_id,
                mention.evidence_id,
                canonical_case_id,
                "dissociation",
                NEGATIVE_MEMBERSHIP_CONFIDENCE,
                "dissociation",
                status="negative",
            )
        elif mention.extraction_location == "quoted_body":
            added = await _add_membership(
                db,
                tenant_id,
                mention.evidence_id,
                canonical_case_id,
                "mentioned_only",
                QUOTED_MENTION_CONFIDENCE,
                "quoted_body",
            )
        else:
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


NEGATIVE_MEMBERSHIP_CONFIDENCE = 0.7


async def _thread_negated_case_ids(
    db: AsyncSession, tenant_id: uuid.UUID, thread_id: uuid.UUID | None
) -> set[uuid.UUID]:
    """Cases some message in this thread explicitly dissociated from.
    Automatic linking of (this thread, that case) requires review from
    then on — a severed link must not quietly regrow (A7)."""
    if thread_id is None:
        return set()
    rows = (
        await db.execute(
            select(EvidenceCaseMembership.canonical_case_id)
            .join(EvidenceItem, EvidenceCaseMembership.evidence_id == EvidenceItem.id)
            .where(
                EvidenceCaseMembership.tenant_id == tenant_id,
                EvidenceCaseMembership.status == "negative",
                EvidenceItem.tenant_id == tenant_id,
                EvidenceItem.thread_id == thread_id,
            )
        )
    ).scalars().all()
    return set(rows)


async def bridge_conversational_mentions(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    evidence: EvidenceItem,
    payload: dict | None = None,
) -> dict:
    """Called when CONVERSATIONAL evidence correlates: extract quoted
    ticket numbers, resolve against registered identifiers, attach
    memberships (or store pending mentions). Never unions cases."""
    counts = {"memberships": 0, "pending": 0, "digest_downgraded": False}

    bot = is_bot_message(payload)
    card_tokens = extract_bot_card_tokens(payload) if bot else []
    subject_tokens = extract_ticket_tokens(evidence.title)
    body_pairs = [
        (t, quoted)
        for t, quoted in extract_ticket_tokens_with_spans(evidence.body_text)
        if t not in subject_tokens and t not in card_tokens
    ]
    if not subject_tokens and not body_pairs and not card_tokens:
        return counts

    located = (
        [("bot_card", t) for t in card_tokens]
        + [("subject", t) for t in subject_tokens if t not in card_tokens]
        + [("quoted_body" if quoted else "body", t) for t, quoted in body_pairs]
    )

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

    # Negative branch (A7): a dissociative message naming a ticket is
    # evidence AGAINST the link — "not related to INC0010427" must never
    # become an explicit_reference. Resolved tokens become negative
    # rows; unresolved ones become dissociation-tagged pending mentions
    # so a late-arriving ticket reconciles to a negative row too.
    # Quoted tokens are excluded entirely (A5): quoting someone else's
    # dissociation is not the author dissociating.
    if states_dissociation(evidence):
        resolved = [r for r in resolved if r[0] != "quoted_body"]
        unresolved = [u for u in unresolved if u[0] != "quoted_body"]
        for location, _token, case_id in resolved:
            if await _add_membership(
                db,
                tenant_id,
                evidence.id,
                case_id,
                "dissociation",
                getattr(evidence, "message_function_confidence", None)
                or NEGATIVE_MEMBERSHIP_CONFIDENCE,
                "dissociation",
                status="negative",
            ):
                counts["negated"] = counts.get("negated", 0) + 1
        for location, token in unresolved:
            try:
                async with db.begin_nested():
                    db.add(
                        PendingIdentifierMention(
                            tenant_id=tenant_id,
                            evidence_id=evidence.id,
                            normalized_value=token,
                            extraction_location="dissociation",
                        )
                    )
                    await db.flush()
                counts["pending"] += 1
            except IntegrityError:
                continue
        return counts

    # Multi-ticket digest guard: many distinct cases in one message is a
    # status report, not an incident conversation. Quoted-only cases
    # count half (A5) — a quoted digest is second-hand twice over.
    fresh_cases = {c for loc, _t, c in resolved if loc != "quoted_body"}
    quoted_only_cases = {
        c for loc, _t, c in resolved if loc == "quoted_body"
    } - fresh_cases
    weighted = len(fresh_cases) + QUOTED_DIGEST_WEIGHT * len(quoted_only_cases)
    is_digest = weighted >= DIGEST_THRESHOLD
    counts["digest_downgraded"] = is_digest

    negated_in_thread = (
        await _thread_negated_case_ids(
            db, tenant_id, getattr(evidence, "thread_id", None)
        )
        if resolved
        else set()
    )
    anchored_cases: set[uuid.UUID] = set()
    for location, _token, case_id in resolved:
        if case_id in negated_in_thread:
            # A message in this thread severed the case; automatic
            # re-linking stays blocked until a reviewer intervenes.
            counts["blocked_by_negative"] = counts.get("blocked_by_negative", 0) + 1
            continue
        relationship = "mentioned_only" if is_digest else "explicit_reference"
        confidence = (
            0.5 if is_digest
            else (SUBJECT_CONFIDENCE if location == "subject" else BODY_CONFIDENCE)
        )
        if location == "quoted_body":
            # Quoted mentions are mentioned_only at most (A5): the
            # author did not assert the link, the quoted text did. They
            # never anchor thread topics either.
            relationship = "mentioned_only"
            confidence = QUOTED_MENTION_CONFIDENCE
        elif location == "bot_card":
            # The ticket system speaking through the bot (A6): near-
            # identifier confidence, and a single-case card CAN anchor
            # the thread topic (the digest guard still applies).
            confidence = BOT_CARD_CONFIDENCE
            if not is_digest:
                anchored_cases.add(case_id)
        elif bot:
            # A bot's own prose is second-hand narration: downweighted,
            # and never a thread-topic anchor by itself.
            confidence = BOT_TEXT_CONFIDENCE
        elif not is_digest:
            anchored_cases.add(case_id)
        if await _add_membership(
            db, tenant_id, evidence.id, case_id, relationship, confidence, location
        ):
            counts["memberships"] += 1
    # A3 topic anchor: exactly one non-digest resolved case is an
    # unambiguous anchor; anything else abstains.
    if len(anchored_cases) == 1:
        counts["anchor_case_id"] = str(next(iter(anchored_cases)))

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


# --- Reply inheritance (conversational tier 1) ------------------------------

# Explicit dissociation language vetoes inheritance: reply structure says
# "same topic", but "different issue, is the ordering DB also down?"
# says otherwise — and language wins. A conservative phrase list is the
# deterministic v1; the future home is the message-function classifier
# (an "explicit_dissociation" function), which will replace this.
DISSOCIATION_PHRASES = (
    "different issue",
    "different problem",
    "unrelated",
    "not related",
    "not this ticket",
    "separate problem",
    "separate issue",
    "wrong incident",
    "wrong ticket",
)

REPLY_INHERITANCE_CONFIDENCE = 0.85

# Below this confidence a classifier label is treated as absent and the
# phrase floor decides. At or above it the classifier's verdict stands
# in BOTH directions: a "dissociation" label vetoes paraphrases the
# phrase list can't see, and a confident non-dissociation label rescues
# false phrase hits ("the outage is not related to load, it's certs").
CLASSIFIER_TRUST_FLOOR = 0.6


def has_dissociation_language(text: str | None) -> bool:
    lowered = (text or "").lower()
    return any(phrase in lowered for phrase in DISSOCIATION_PHRASES)


def states_dissociation(evidence: EvidenceItem) -> bool:
    """Does this message explicitly SEVER a case link? (A7 negative
    branch.) Narrower than the inheritance veto: a correction abstains
    from inheritance but its own ticket mention is a POSITIVE link —
    "Correction — tracking under INC0010455" asserts INC0010455, and
    A2's propagation depends on that membership existing.

    The classifier decides when it produced a confident label; the
    conservative phrase list is the deterministic floor whenever the
    label is missing, out-of-vocabulary, or low-confidence (LLM budget
    exhausted, pre-0041 rows, provider down)."""
    label = getattr(evidence, "message_function", None)
    if label == "dissociation":
        return True
    confidence = getattr(evidence, "message_function_confidence", None) or 0.0
    if label in (None, "unclassified") or confidence < CLASSIFIER_TRUST_FLOOR:
        return has_dissociation_language(evidence.body_text) or has_dissociation_language(
            evidence.title
        )
    return False


def is_dissociative(evidence: EvidenceItem) -> bool:
    """Inheritance-veto verdict (A1): dissociations sever, and a
    confident correction also abstains — it changes what earlier
    messages established, so inheriting the parent's (possibly wrong)
    case is premature; A2's supersession resolves what the correction
    actually establishes."""
    if states_dissociation(evidence):
        return True
    label = getattr(evidence, "message_function", None)
    confidence = getattr(evidence, "message_function_confidence", None) or 0.0
    return label == "correction" and confidence >= CLASSIFIER_TRUST_FLOOR


async def _resolve_parent_evidence(
    db: AsyncSession, tenant_id: uuid.UUID, reply_to: str
) -> uuid.UUID | None:
    """Parent message → parent evidence, scoped to teams sources
    (message ids are the teams external_id namespace)."""
    return (
        await db.execute(
            select(EvidenceItem.id)
            .join(RawEvidenceObject, EvidenceItem.raw_object_ref == RawEvidenceObject.id)
            .join(Source, RawEvidenceObject.source_id == Source.id)
            .where(
                EvidenceItem.tenant_id == tenant_id,
                RawEvidenceObject.tenant_id == tenant_id,
                RawEvidenceObject.external_id == reply_to,
                Source.source_type == "teams",
            )
            .order_by(EvidenceItem.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def inherit_reply_membership(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    evidence: EvidenceItem,
    payload: dict | None,
) -> dict:
    """Deterministic conversational tier 1: a reply to a case-linked
    message inherits that message's case membership.

    Rules (from the review's safe-decision policy):
    - Inherit only when the parent has EXACTLY ONE active,
      non-mentioned-only membership — a reply to a multi-case digest or
      an ambiguous parent abstains.
    - Explicit dissociation language in the reply vetoes inheritance.
    - Chains work naturally: an inherited membership is itself
      inheritable, so the third reply in a thread anchors through the
      second.
    """
    counts = {"inherited": 0, "vetoed": False, "abstained": False}
    p = payload or {}
    reply_to = p.get("reply_to_id")
    if not reply_to:
        return counts

    if is_dissociative(evidence):
        counts["vetoed"] = True
        logger.info(
            "reply_inheritance.vetoed_by_dissociation",
            tenant_id=str(tenant_id),
            evidence_id=str(evidence.id),
        )
        return counts

    parent_evidence_id = await _resolve_parent_evidence(db, tenant_id, str(reply_to))
    if parent_evidence_id is None:
        return counts

    parent_memberships = (
        await db.execute(
            select(EvidenceCaseMembership.canonical_case_id).where(
                EvidenceCaseMembership.tenant_id == tenant_id,
                EvidenceCaseMembership.evidence_id == parent_evidence_id,
                EvidenceCaseMembership.status == "active",
                EvidenceCaseMembership.relationship_type != "mentioned_only",
            ).limit(3)
        )
    ).scalars().all()
    distinct_cases = set(parent_memberships)
    if len(distinct_cases) != 1:
        # No anchor, or a multi-case parent — the single-case-topic rule
        # abstains rather than guessing which case the reply continues.
        counts["abstained"] = len(distinct_cases) > 1
        return counts

    case_id = next(iter(distinct_cases))
    if case_id in await _thread_negated_case_ids(
        db, tenant_id, getattr(evidence, "thread_id", None)
    ):
        counts["blocked_by_negative"] = True
        return counts

    inherit_confidence = (
        BOT_REPLY_INHERITANCE_CONFIDENCE
        if is_bot_message(payload)
        else REPLY_INHERITANCE_CONFIDENCE
    )
    if await _add_membership(
        db,
        tenant_id,
        evidence.id,
        case_id,
        "reply_inheritance",
        inherit_confidence,
        "reply_structure",
    ):
        counts["inherited"] = 1
    return counts


# --- Correction supersession (A2) -------------------------------------------

# Membership relationship types a chat correction may retire. A ticket's
# own primary_case row is never correctable from conversation — the
# ticket system is authoritative for its own case (P4 authority rule).
CORRECTABLE_RELATIONSHIPS = ("explicit_reference", "reply_inheritance")
CORRECTED_MEMBERSHIP_CONFIDENCE = 0.8


async def apply_correction(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    evidence: EvidenceItem,
    payload: dict | None,
) -> dict:
    """A confident correction message retires what its target message
    established and, when unambiguous, re-links the target to the
    corrected case (backlog A2).

    - Target = the replied-to message when reply structure exists,
      otherwise the most recent other message in the same thread.
    - Superseded rows keep their history: ``status='corrected'`` — never
      deleted. Only conversational relationship types are correctable;
      a ticket's primary_case row is not.
    - Propagation: only when the correction itself resolved EXACTLY ONE
      active case of its own ("Correction — tracking under INC0010455")
      does the target gain that case (explicit_reference at reduced
      confidence, extraction_location='correction'). A vague correction
      ("that's the wrong ticket") supersedes without re-linking.
    """
    counts = {"superseded": 0, "propagated": 0, "target_found": False}
    label = getattr(evidence, "message_function", None)
    confidence = getattr(evidence, "message_function_confidence", None) or 0.0
    if label != "correction" or confidence < CLASSIFIER_TRUST_FLOOR:
        return counts

    target_id: uuid.UUID | None = None
    reply_to = (payload or {}).get("reply_to_id")
    if reply_to:
        target_id = await _resolve_parent_evidence(db, tenant_id, str(reply_to))
    if target_id is None and evidence.thread_id is not None:
        target_id = (
            await db.execute(
                select(EvidenceItem.id)
                .where(
                    EvidenceItem.tenant_id == tenant_id,
                    EvidenceItem.thread_id == evidence.thread_id,
                    EvidenceItem.id != evidence.id,
                )
                .order_by(EvidenceItem.ingested_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
    if target_id is None:
        return counts
    counts["target_found"] = True

    superseded_case_ids: list[uuid.UUID] = []
    target_memberships = (
        (
            await db.execute(
                select(EvidenceCaseMembership).where(
                    EvidenceCaseMembership.tenant_id == tenant_id,
                    EvidenceCaseMembership.evidence_id == target_id,
                    EvidenceCaseMembership.status == "active",
                    EvidenceCaseMembership.relationship_type.in_(
                        CORRECTABLE_RELATIONSHIPS
                    ),
                )
            )
        )
        .scalars()
        .all()
    )
    for membership in target_memberships:
        membership.status = "corrected"
        superseded_case_ids.append(membership.canonical_case_id)
        counts["superseded"] += 1

    # The correction's own case (written by bridge_conversational_mentions
    # just before this runs) — exactly one, or no propagation.
    own_cases = set(
        (
            await db.execute(
                select(EvidenceCaseMembership.canonical_case_id).where(
                    EvidenceCaseMembership.tenant_id == tenant_id,
                    EvidenceCaseMembership.evidence_id == evidence.id,
                    EvidenceCaseMembership.status == "active",
                    EvidenceCaseMembership.relationship_type != "mentioned_only",
                ).limit(3)
            )
        )
        .scalars()
        .all()
    )
    corrected_case_id: uuid.UUID | None = None
    if len(own_cases) == 1:
        corrected_case_id = next(iter(own_cases))
        if await _add_membership(
            db,
            tenant_id,
            target_id,
            corrected_case_id,
            "explicit_reference",
            CORRECTED_MEMBERSHIP_CONFIDENCE,
            "correction",
        ):
            counts["propagated"] = 1

    if counts["superseded"] or counts["propagated"]:
        from contextedge.services.event_log_service import append_operational_event

        await append_operational_event(
            db,
            tenant_id=tenant_id,
            entity_type="evidence",
            entity_id=target_id,
            event_type="correlation.correction_applied",
            payload={
                "correction_evidence_id": str(evidence.id),
                "superseded_case_ids": [str(c) for c in superseded_case_ids],
                "corrected_case_id": (
                    str(corrected_case_id) if corrected_case_id else None
                ),
                "superseded": counts["superseded"],
            },
        )
        logger.info(
            "correction.applied",
            tenant_id=str(tenant_id),
            correction_evidence_id=str(evidence.id),
            target_evidence_id=str(target_id),
            superseded=counts["superseded"],
            propagated=counts["propagated"],
        )
    if corrected_case_id is not None:
        counts["corrected_case_id"] = str(corrected_case_id)
    return counts
