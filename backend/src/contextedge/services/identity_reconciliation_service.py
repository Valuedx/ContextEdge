"""Find identities that are the same thing under different names.

The layered resolver in ``identity_service`` decides one mention at a
time, against candidates that share a substring with it. That is the
right shape for the hot path and it has a hard ceiling: when a mention
shares no substring with the identity it belongs to, no candidate is
generated, the adjudicator is never consulted, and a second identity is
created silently.

Measured on a live tenant: of 204 mention→identity links, 117 resolved
as ``provisional_new`` and only 7 were decided by the adjudicator. The
graph accordingly holds ``SFA`` beside ``Sales Force Automation`` and
``HP UPD`` beside ``HP Universal Print Driver`` — pairs no amount of
per-mention judgement could have caught, because the two were never
presented together.

This pass presents them together. It reads a whole entity type at once
and looks across it.

**It proposes; it never merges.** ``merge_canonical_identities``
re-points aliases and deactivates a row, and a wrong merge destroys the
distinction between two real systems in a way that is invisible
afterwards. Proposals land in ``identity_merge_proposals`` for a human,
who already has a merge control on the identities page.

Proposals are persisted rather than recomputed for a reason that only
shows up on the second run: without a durable record, a scheduled job
re-raises every pair a reviewer has already rejected, forever.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import structlog
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.models.episode import (
    CanonicalIdentity,
    IdentityAlias,
    IdentityMergeProposal,
)

logger = structlog.get_logger()

# Identities of one type sent to the model in a single call. The whole
# point is cross-set visibility, so this wants to be large — but a set
# that does not fit in one call is worse than one that does, and recall
# degrades in a long list. Splitting means a duplicate pair straddling
# the split is missed, which is the failure this pass exists to prevent,
# so batches overlap by design (see ``_batches``).
BATCH_SIZE = 60
BATCH_OVERLAP = 10

# The model is told abstaining is free, so anything short of near
# certainty means it guessed rather than declined — and a reviewer's
# attention is the scarce resource this pass spends.
#
# 0.95 rather than a softer bar because the live graph separated cleanly
# at it. Every proposal the first run scored 1.00 was correct (SFA /
# Sales Force Automation, HP UPD / HP Universal Print Driver, agent /
# Agents, prod / production). Everything it scored 0.90 was a role or
# placeholder that should not have been an entity at all, or an outright
# error: folding a general "Spooler service" into "Spooler service on
# PRINTSRV04", which narrows a service to one host.
MIN_CONFIDENCE = 0.95

# Which identities are eligible. Trusted rows are left alone — they have
# been resolved deterministically or reviewed, and folding one away on a
# model's suggestion would undo that.
ELIGIBLE_STATES = ("provisional", "needs_review")

MAX_ALIASES_SHOWN = 4


@dataclass(slots=True)
class ProposedMerge:
    keep_id: uuid.UUID
    merge_id: uuid.UUID
    entity_type: str
    confidence: float
    reason: str


def _batches(items: list, size: int, overlap: int) -> list[list]:
    """Overlapping windows.

    A plain split would hide any duplicate pair that lands either side of
    a boundary — and since the list is ordered by name, near-duplicates
    are exactly what clusters at boundaries. Overlap costs a few repeated
    rows per call and removes a whole class of silent miss.
    """
    if len(items) <= size:
        return [items]
    step = max(size - overlap, 1)
    return [items[start : start + size] for start in range(0, len(items), step)]


def _render(
    identity: CanonicalIdentity,
    aliases: list[str],
    index: int,
    show_type: bool = False,
) -> str:
    line = f"{index}. {identity.canonical_name}"
    if show_type:
        # Only when the batch spans types. Otherwise it is noise on every
        # line, and a constant repeated per record reads as a signal.
        line += f"  [recorded as: {identity.entity_type}]"
    if aliases:
        line += f"  (also known as: {', '.join(aliases[:MAX_ALIASES_SHOWN])})"
    context = (identity.metadata_extra or {}).get("context")
    if context:
        line += f"  — {str(context)[:120]}"
    return line


async def _load_candidates(
    db: AsyncSession, tenant_id: uuid.UUID, entity_types: list[str]
) -> tuple[list[CanonicalIdentity], dict[uuid.UUID, list[str]]]:
    identities = (
        (
            await db.execute(
                select(CanonicalIdentity)
                .where(
                    CanonicalIdentity.tenant_id == tenant_id,
                    CanonicalIdentity.entity_type.in_(entity_types),
                    CanonicalIdentity.is_active.is_(True),
                    CanonicalIdentity.resolution_state.in_(ELIGIBLE_STATES),
                )
                .order_by(CanonicalIdentity.normalized_name)
            )
        )
        .scalars()
        .all()
    )
    if not identities:
        return [], {}

    rows = (
        await db.execute(
            select(IdentityAlias.canonical_identity_id, IdentityAlias.alias_text).where(
                IdentityAlias.canonical_identity_id.in_([i.id for i in identities])
            )
        )
    ).all()
    aliases: dict[uuid.UUID, list[str]] = {}
    for identity_id, alias_text in rows:
        bucket = aliases.setdefault(identity_id, [])
        if alias_text and alias_text not in bucket:
            bucket.append(alias_text)
    return list(identities), aliases


async def _ask(
    entity_type: str,
    identities: list[CanonicalIdentity],
    aliases: dict[uuid.UUID, list[str]],
    *,
    tenant_id: uuid.UUID,
    db: AsyncSession,
) -> list[dict]:
    from contextedge.ai.prompts import get_prompt
    from contextedge.ai.provider import llm_complete_json

    prompt = get_prompt("identity_reconciliation", tenant_id)
    mixed = len({identity.entity_type for identity in identities}) > 1
    listing = "\n".join(
        _render(identity, aliases.get(identity.id, []), index, show_type=mixed)
        for index, identity in enumerate(identities)
    )
    try:
        result = await llm_complete_json(
            prompt.format_user(entity_type=entity_type, records=listing),
            task="extraction",
            system_prompt=prompt.system,
            tenant_id=tenant_id,
            db=db,
            prompt_name=prompt.name,
            prompt_version=prompt.version,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "identity_reconciliation.call_failed",
            entity_type=entity_type,
            error_type=type(exc).__name__,
        )
        return []
    if not isinstance(result, dict):
        return []
    groups = result.get("groups")
    return groups if isinstance(groups, list) else []


def _type_groups(entity_types: list[str]) -> list[list[str]]:
    """Collapse confusable types into shared passes.

    ``["application", "device", "service"]`` becomes
    ``[["application", "service"], ["device"]]`` — so the two types the
    extractor labels interchangeably are compared against each other,
    and everything else keeps its own pass.
    """
    from contextedge.services.identity_service import compatible_entity_types

    groups: list[list[str]] = []
    placed: set[str] = set()
    for entity_type in entity_types:
        if entity_type in placed:
            continue
        # Only types this tenant actually has; an empty sibling would
        # just widen the query for nothing.
        group = [t for t in compatible_entity_types(entity_type) if t in entity_types]
        placed.update(group)
        groups.append(group or [entity_type])
    return groups


def _parse_groups(
    groups: list[dict],
    identities: list[CanonicalIdentity],
    entity_type: str | None = None,
) -> list[ProposedMerge]:
    """Turn the model's index references into pairs, dropping anything
    that does not survive checking.

    Indices are validated rather than trusted: a hallucinated index would
    otherwise silently propose merging two rows the model never saw.
    """
    proposals: list[ProposedMerge] = []
    for group in groups:
        if not isinstance(group, dict):
            continue
        confidence = float(group.get("confidence") or 0.0)
        if confidence < MIN_CONFIDENCE:
            continue

        keep_index = group.get("keep_id")
        merge_indices = group.get("merge_ids") or []
        if not isinstance(merge_indices, list):
            continue
        try:
            keep = identities[int(keep_index)]
        except (TypeError, ValueError, IndexError):
            continue

        for raw in merge_indices:
            try:
                duplicate = identities[int(raw)]
            except (TypeError, ValueError, IndexError):
                continue
            if duplicate.id == keep.id:
                continue
            reason = str(group.get("reason") or "")[:500]
            if keep.entity_type != duplicate.entity_type:
                # Say so in the proposal. A cross-type merge changes what
                # KIND of thing the surviving record is recorded as, and a
                # reviewer approving "fold X into Y" deserves to know that
                # is part of what they are approving.
                reason = (
                    f"{reason} (recorded as {duplicate.entity_type}; would "
                    f"become {keep.entity_type})"
                ).strip()

            proposals.append(
                ProposedMerge(
                    keep_id=keep.id,
                    merge_id=duplicate.id,
                    # The keeper's type survives the merge, so that is the
                    # type this proposal is about.
                    entity_type=entity_type or keep.entity_type,
                    confidence=confidence,
                    reason=reason,
                )
            )
    return proposals


async def _existing_pairs(
    db: AsyncSession, tenant_id: uuid.UUID
) -> set[tuple[uuid.UUID, uuid.UUID]]:
    """Every pair already proposed, in either direction and whatever its
    status.

    Direction matters: a reviewer who rejected "fold A into B" has
    rejected the claim that A and B are the same thing, and re-proposing
    it the other way round would put the same question back in their
    queue wearing a hat.
    """
    rows = (
        await db.execute(
            select(
                IdentityMergeProposal.primary_identity_id,
                IdentityMergeProposal.duplicate_identity_id,
            ).where(IdentityMergeProposal.tenant_id == tenant_id)
        )
    ).all()
    pairs: set[tuple[uuid.UUID, uuid.UUID]] = set()
    for primary, duplicate in rows:
        pairs.add((primary, duplicate))
        pairs.add((duplicate, primary))
    return pairs


async def reconcile_identities(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    entity_types: list[str] | None = None,
    persist: bool = True,
) -> list[ProposedMerge]:
    """Propose merges across a tenant's unresolved identities.

    Returns what it proposed. ``persist=False`` runs the analysis without
    writing, for evaluating a prompt change against a real graph.
    """
    if entity_types is None:
        rows = (
            (
                await db.execute(
                    select(CanonicalIdentity.entity_type)
                    .where(
                        CanonicalIdentity.tenant_id == tenant_id,
                        CanonicalIdentity.is_active.is_(True),
                        CanonicalIdentity.resolution_state.in_(ELIGIBLE_STATES),
                    )
                    .distinct()
                )
            )
            .scalars()
            .all()
        )
        entity_types = sorted(str(r) for r in rows if r)

    seen = await _existing_pairs(db, tenant_id) if persist else set()
    all_proposals: list[ProposedMerge] = []

    # Confusable types are reconciled TOGETHER, in one pass over their
    # union. Reconciling each type separately is why "JMX" the service
    # and "JMX" the application could sit side by side in the graph
    # indefinitely: the two were never in the same batch, so nothing ever
    # looked at them at once. Same failure as per-mention adjudication,
    # one level up.
    for group in _type_groups(entity_types):
        identities, aliases = await _load_candidates(db, tenant_id, group)
        if len(identities) < 2:
            continue

        label = " + ".join(group)
        for batch in _batches(identities, BATCH_SIZE, BATCH_OVERLAP):
            groups = await _ask(label, batch, aliases, tenant_id=tenant_id, db=db)
            for proposal in _parse_groups(groups, batch, None):
                pair = (proposal.keep_id, proposal.merge_id)
                if pair in seen:
                    continue
                seen.add(pair)
                seen.add((proposal.merge_id, proposal.keep_id))
                all_proposals.append(proposal)
                if persist:
                    db.add(
                        IdentityMergeProposal(
                            tenant_id=tenant_id,
                            primary_identity_id=proposal.keep_id,
                            duplicate_identity_id=proposal.merge_id,
                            entity_type=proposal.entity_type,
                            confidence=proposal.confidence,
                            reason=proposal.reason,
                            status="pending",
                            proposed_by="identity_reconciliation",
                        )
                    )

    if persist and all_proposals:
        await db.flush()

    logger.info(
        "identity_reconciliation.completed",
        tenant_id=str(tenant_id),
        entity_types=len(entity_types),
        proposals=len(all_proposals),
    )
    return all_proposals


async def decide_proposal(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    proposal_id: uuid.UUID,
    *,
    accept: bool,
    actor_id: uuid.UUID | None,
) -> IdentityMergeProposal | None:
    """Apply or reject a proposal. Accepting performs the merge.

    The merge itself still goes through ``merge_canonical_identities``,
    so alias re-pointing and the operational event are identical to a
    merge a human initiates by hand — this path decides, it does not
    reimplement.
    """
    from datetime import UTC, datetime

    from contextedge.services.identity_service import merge_canonical_identities

    proposal = await db.get(IdentityMergeProposal, proposal_id)
    if proposal is None or proposal.tenant_id != tenant_id:
        return None
    if proposal.status != "pending":
        return proposal

    if accept:
        await merge_canonical_identities(
            db,
            tenant_id=tenant_id,
            primary_identity_id=proposal.primary_identity_id,
            duplicate_identity_id=proposal.duplicate_identity_id,
            actor_id=actor_id,
        )

    proposal.status = "accepted" if accept else "rejected"
    proposal.decided_by = actor_id
    proposal.decided_at = datetime.now(UTC)
    await db.flush()
    return proposal
