"""Conversational-reference resolver (backlog A4).

"Can you look at John's ticket?" and "any update on the ticket for the
ordering server?" carry no ticket number — but they refer to one.
Resolution is deterministic candidate generation with hard abstention
(Doc-1's appendix): indexes, not free-form guessing.

Precision discipline, in order:
1. **Trigger required** — a possessive-ticket pattern ("John's ticket")
   or a ticket-for pattern ("the ticket for X"). No trigger, no lookup:
   a message merely mentioning John must never link his cases.
2. **Candidates come from the identity layer** — only trusted
   identities ALREADY linked to this message by extraction qualify.
   The resolver never searches the identity table by free text.
3. **Exactly one active case or nothing** — a person working three
   open tickets abstains (logged); so does an entity with two.

Person path: person identity → its ticket evidence (identity links) →
primary-case memberships, within the activity window.
Entity path: non-person identity → same-named Entity → reverse
``affects_ci`` edges → ticket evidence → primary-case memberships.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.models.case_bridge import EvidenceCaseMembership
from contextedge.models.entity import Entity
from contextedge.models.episode import CanonicalIdentity, EvidenceIdentityLink
from contextedge.models.evidence import EvidenceItem
from contextedge.models.pattern import GraphEdge
from contextedge.services.ticket_bridge_service import (
    _add_membership,
    _thread_negated_case_ids,
    states_dissociation,
)

logger = structlog.get_logger()

# A case is "active" for reference resolution when it has evidence this
# recent — conversational shorthand refers to what is being worked NOW.
ACTIVE_CASE_WINDOW_DAYS = 14
REFERENCE_CONFIDENCE = 0.8

# "John's ticket", "Maria Garcia's incident" — the name is validated
# against identities linked to the message, never used as a search key.
_POSSESSIVE_RE = re.compile(
    r"\b([A-Z][\w.-]+(?:\s+[A-Z][\w.-]+){0,2})'s\s+(?:ticket|incident|case)\b"
)
# "the ticket for the ordering server", "incident on vpn-gw-emea-03"
_TICKET_FOR_RE = re.compile(
    r"\b(?:ticket|incident|case)\s+(?:for|about|on)\s+(?:the\s+)?([\w][\w /.-]{2,60})",
    re.IGNORECASE,
)


def extract_reference_triggers(text: str | None) -> dict:
    """Trigger phrases, split by path. Deterministic; no lookups."""
    t = text or ""
    return {
        "person_names": [m.group(1) for m in _POSSESSIVE_RE.finditer(t)],
        "entity_phrases": [
            m.group(1).strip().lower() for m in _TICKET_FOR_RE.finditer(t)
        ],
    }


async def _linked_trusted_identities(
    db: AsyncSession, tenant_id: uuid.UUID, evidence_id: uuid.UUID
) -> list[CanonicalIdentity]:
    return list(
        (
            await db.execute(
                select(CanonicalIdentity)
                .join(
                    EvidenceIdentityLink,
                    EvidenceIdentityLink.identity_id == CanonicalIdentity.id,
                )
                .where(
                    EvidenceIdentityLink.tenant_id == tenant_id,
                    EvidenceIdentityLink.evidence_id == evidence_id,
                    CanonicalIdentity.tenant_id == tenant_id,
                    CanonicalIdentity.is_active.is_(True),
                    CanonicalIdentity.resolution_state.in_(
                        ("resolved", "verified")
                    ),
                )
            )
        )
        .scalars()
        .all()
    )


async def _active_cases_for_person(
    db: AsyncSession, tenant_id: uuid.UUID, identity_id: uuid.UUID
) -> set[uuid.UUID]:
    cutoff = datetime.now(UTC) - timedelta(days=ACTIVE_CASE_WINDOW_DAYS)
    rows = (
        await db.execute(
            select(EvidenceCaseMembership.canonical_case_id)
            .join(
                EvidenceIdentityLink,
                EvidenceIdentityLink.evidence_id
                == EvidenceCaseMembership.evidence_id,
            )
            .join(
                EvidenceItem,
                EvidenceItem.id == EvidenceCaseMembership.evidence_id,
            )
            .where(
                EvidenceCaseMembership.tenant_id == tenant_id,
                EvidenceCaseMembership.relationship_type == "primary_case",
                EvidenceCaseMembership.status == "active",
                EvidenceIdentityLink.tenant_id == tenant_id,
                EvidenceIdentityLink.identity_id == identity_id,
                EvidenceItem.ingested_at >= cutoff,
            )
            .limit(6)
        )
    ).scalars().all()
    return set(rows)


async def _active_cases_for_entity_name(
    db: AsyncSession, tenant_id: uuid.UUID, name: str
) -> set[uuid.UUID]:
    entity_ids = (
        await db.execute(
            select(Entity.id).where(
                Entity.tenant_id == tenant_id,
                func.lower(Entity.name) == name.lower(),
            ).limit(3)
        )
    ).scalars().all()
    if not entity_ids:
        return set()
    cutoff = datetime.now(UTC) - timedelta(days=ACTIVE_CASE_WINDOW_DAYS)
    rows = (
        await db.execute(
            select(EvidenceCaseMembership.canonical_case_id)
            .join(
                GraphEdge,
                GraphEdge.source_node_id == EvidenceCaseMembership.evidence_id,
            )
            .join(
                EvidenceItem,
                EvidenceItem.id == EvidenceCaseMembership.evidence_id,
            )
            .where(
                EvidenceCaseMembership.tenant_id == tenant_id,
                EvidenceCaseMembership.relationship_type == "primary_case",
                EvidenceCaseMembership.status == "active",
                GraphEdge.tenant_id == tenant_id,
                GraphEdge.edge_type == "affects_ci",
                GraphEdge.source_node_type == "evidence",
                GraphEdge.target_node_type == "entity",
                GraphEdge.target_node_id.in_(tuple(entity_ids)),
                GraphEdge.valid_to.is_(None),
                EvidenceItem.ingested_at >= cutoff,
            )
            .limit(6)
        )
    ).scalars().all()
    return set(rows)


def _name_matches(identity: CanonicalIdentity, trigger_name: str) -> bool:
    """Case-insensitive: the trigger name equals the canonical name or
    its first token ("John" matches "John Smith" — but if TWO linked
    Johns match, the caller's ambiguity rule abstains)."""
    canonical = (identity.canonical_name or "").lower()
    trigger = trigger_name.lower()
    return canonical == trigger or canonical.split(" ")[0] == trigger


async def resolve_conversational_references(
    db: AsyncSession, tenant_id: uuid.UUID, evidence: EvidenceItem
) -> dict:
    """Resolve indirect references on one conversational message. Only
    runs when the message is not already anchored (the caller checks
    memberships); writes at most ONE membership — multiple triggers
    resolving to different cases abstain entirely."""
    counts = {"resolved": 0, "abstained": 0, "no_candidates": 0}
    # A dissociative message ("that's not John's ticket") must never
    # resolve the very link it denies.
    if states_dissociation(evidence):
        return counts
    text = " ".join(filter(None, [evidence.title, evidence.body_text]))
    triggers = extract_reference_triggers(text)
    if not triggers["person_names"] and not triggers["entity_phrases"]:
        return counts

    identities = await _linked_trusted_identities(db, tenant_id, evidence.id)
    resolved_cases: set[uuid.UUID] = set()

    for trigger_name in triggers["person_names"]:
        matches = [
            i
            for i in identities
            if i.entity_type == "person" and _name_matches(i, trigger_name)
        ]
        if len(matches) != 1:
            if len(matches) > 1:
                counts["abstained"] += 1
            continue
        cases = await _active_cases_for_person(db, tenant_id, matches[0].id)
        if len(cases) == 1:
            resolved_cases.add(next(iter(cases)))
        elif len(cases) > 1:
            counts["abstained"] += 1
            logger.info(
                "conversational_reference.ambiguous_person",
                tenant_id=str(tenant_id),
                evidence_id=str(evidence.id),
                identity_id=str(matches[0].id),
                case_count=len(cases),
            )
        else:
            counts["no_candidates"] += 1

    for phrase in triggers["entity_phrases"]:
        matches = [
            i
            for i in identities
            if i.entity_type != "person"
            and (i.canonical_name or "").lower() in (phrase, phrase.rstrip("?.!,"))
        ]
        if len(matches) != 1:
            if len(matches) > 1:
                counts["abstained"] += 1
            continue
        cases = await _active_cases_for_entity_name(
            db, tenant_id, matches[0].canonical_name
        )
        if len(cases) == 1:
            resolved_cases.add(next(iter(cases)))
        elif len(cases) > 1:
            counts["abstained"] += 1
            logger.info(
                "conversational_reference.ambiguous_entity",
                tenant_id=str(tenant_id),
                evidence_id=str(evidence.id),
                entity_name=matches[0].canonical_name,
                case_count=len(cases),
            )
        else:
            counts["no_candidates"] += 1

    if len(resolved_cases) != 1:
        if len(resolved_cases) > 1:
            counts["abstained"] += 1
        return counts

    # A7 fence: a case this thread explicitly severed stays severed.
    case_id = next(iter(resolved_cases))
    thread_id = getattr(evidence, "thread_id", None)
    if thread_id is not None and case_id in await _thread_negated_case_ids(
        db, tenant_id, thread_id
    ):
        counts["abstained"] += 1
        return counts

    if await _add_membership(
        db,
        tenant_id,
        evidence.id,
        case_id,
        "explicit_reference",
        REFERENCE_CONFIDENCE,
        "conversational_reference",
    ):
        counts["resolved"] = 1
    return counts
