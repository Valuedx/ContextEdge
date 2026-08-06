"""Issue-signature extraction + recurrence linking (backlog B3 + C2).

Runs per APPROVED episode (dispatched by the approval endpoint):
1. LLM extracts the generalized fingerprint; a strict-structure /
   lenient-vocabulary Pydantic gate validates it (the P4 pattern).
2. The normalized ``signature_key`` finds-or-creates the tenant's
   signature row; the episode links to it (idempotent).
3. **Recurrence (C2)**: when the signature already has other episodes,
   the new episode's seed evidence gains a ``recurrence`` membership to
   the most recent prior episode's case — similar problem, NEVER the
   same occurrence. The cluster resolver deliberately does not expand
   through recurrence memberships; they exist for precedent retrieval
   and the applicability ladder (B4).
"""

from __future__ import annotations

import re
import uuid

import structlog
from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.ai.prompts import get_prompt
from contextedge.ai.provider import llm_complete_json
from contextedge.models.case_bridge import EvidenceCaseMembership
from contextedge.models.episode import Episode
from contextedge.models.issue_signature import EpisodeIssueSignature, IssueSignature
from contextedge.services.ticket_bridge_service import _add_membership

logger = structlog.get_logger()

RECURRENCE_CONFIDENCE = 0.6
_SLUG_RE = re.compile(r"[^a-z0-9]+")

ENVIRONMENTS = ("production", "corporate_managed", "development")
SCOPES = ("single_device", "multiple_devices", "site_wide", "service_wide")


def _slug(value: str | None) -> str:
    return _SLUG_RE.sub("_", (value or "").strip().lower()).strip("_")


class IssueSignatureDraft(BaseModel):
    """Strict about structure, lenient about vocabulary (P4 gate
    pattern): out-of-list environment/scope null out; confidence
    clamps; capability and failure mode are required non-empty."""

    affected_capability: str = Field(min_length=2, max_length=80)
    failing_component: str | None = Field(default=None, max_length=120)
    failure_mode: str = Field(min_length=2, max_length=120)
    trigger_change: str | None = Field(default=None, max_length=200)
    environment: str | None = None
    scope: str | None = None
    confidence: float = 0.5

    @field_validator("environment")
    @classmethod
    def _known_environment(cls, v: str | None) -> str | None:
        return v if v in ENVIRONMENTS else None

    @field_validator("scope")
    @classmethod
    def _known_scope(cls, v: str | None) -> str | None:
        return v if v in SCOPES else None

    @field_validator("confidence")
    @classmethod
    def _clamp(cls, v: float) -> float:
        return min(max(v, 0.0), 1.0)


def signature_key_for(draft: IssueSignatureDraft) -> str:
    """Normalized dedupe key: capability|component|failure_mode. The
    trigger/environment/scope are descriptive, not identity — two
    episodes of the same failure triggered differently still recur."""
    return "|".join(
        [
            _slug(draft.affected_capability),
            _slug(draft.failing_component) or "-",
            _slug(draft.failure_mode),
        ]
    )[:240]


async def extract_issue_signature(
    db: AsyncSession, tenant_id: uuid.UUID, episode_id: uuid.UUID
) -> dict:
    counts = {"status": "skipped", "recurrence_links": 0}
    episode = await db.get(Episode, episode_id)
    if (
        episode is None
        or episode.tenant_id != tenant_id
        or episode.reviewer_state != "approved"
    ):
        counts["reason"] = "not_approved_or_missing"
        return counts

    existing_link = (
        await db.execute(
            select(EpisodeIssueSignature.id).where(
                EpisodeIssueSignature.episode_id == episode_id
            ).limit(1)
        )
    ).scalar_one_or_none()
    if existing_link is not None:
        counts["reason"] = "already_extracted"
        return counts

    # Explicit step query: episode.steps is a lazy relationship and an
    # async session raises on implicit IO.
    from contextedge.models.episode import EpisodeStep

    steps = (
        (
            await db.execute(
                select(EpisodeStep)
                .where(EpisodeStep.episode_id == episode_id)
                .order_by(EpisodeStep.step_order.asc())
                .limit(15)
            )
        )
        .scalars()
        .all()
    )
    steps_text = "\n".join(f"- {s.step_type}: {s.text}" for s in steps)
    prompt = get_prompt("issue_signature", tenant_id)
    raw = await llm_complete_json(
        prompt.format_user(
            title=episode.title or "",
            root_cause=episode.root_cause_summary or "",
            outcome=episode.final_outcome or "",
            steps=steps_text,
        ),
        task="extraction",
        system_prompt=prompt.system,
        tenant_id=tenant_id,
        db=db,
        prompt_name=prompt.name,
        prompt_version=prompt.version,
    )
    try:
        draft = IssueSignatureDraft.model_validate(raw or {})
    except ValidationError as exc:
        logger.warning(
            "issue_signature.invalid_draft",
            tenant_id=str(tenant_id),
            episode_id=str(episode_id),
            errors=exc.error_count(),
        )
        counts["reason"] = "invalid_draft"
        return counts

    key = signature_key_for(draft)
    signature = (
        await db.execute(
            select(IssueSignature).where(
                IssueSignature.tenant_id == tenant_id,
                IssueSignature.signature_key == key,
            )
        )
    ).scalar_one_or_none()
    is_new = signature is None
    if is_new:
        signature = IssueSignature(
            tenant_id=tenant_id,
            signature_key=key,
            affected_capability=_slug(draft.affected_capability),
            failing_component=_slug(draft.failing_component) or None,
            failure_mode=_slug(draft.failure_mode),
            trigger_change=draft.trigger_change,
            environment=draft.environment,
            scope=draft.scope,
        )
        try:
            async with db.begin_nested():
                db.add(signature)
                await db.flush()
        except IntegrityError:
            signature = (
                await db.execute(
                    select(IssueSignature).where(
                        IssueSignature.tenant_id == tenant_id,
                        IssueSignature.signature_key == key,
                    )
                )
            ).scalar_one()
            is_new = False
    if not is_new:
        signature.episode_count = (signature.episode_count or 1) + 1

    try:
        async with db.begin_nested():
            db.add(
                EpisodeIssueSignature(
                    tenant_id=tenant_id,
                    episode_id=episode_id,
                    issue_signature_id=signature.id,
                    confidence=draft.confidence,
                )
            )
            await db.flush()
    except IntegrityError:
        counts["reason"] = "already_extracted"
        return counts

    # Graph edge alongside the relational link (roadmap D2): the
    # projection traverses graph_edges only, so without this the
    # signature-seeded agent could see a signature but never reach the
    # episodes behind it. Fail-soft — the relational link above is the
    # authoritative record and the D2 backfill can heal a missed edge;
    # failing extraction over graph bookkeeping would be backwards.
    try:
        from contextedge.graph.builder import ensure_edge

        await ensure_edge(
            db,
            tenant_id,
            source_type="episode",
            source_id=episode_id,
            target_type="issue_signature",
            target_id=signature.id,
            edge_type="has_signature",
            weight=1.0,
            confidence=draft.confidence,
        )
    except Exception as edge_exc:
        logger.warning(
            "issue_signature.edge_write_failed",
            episode_id=str(episode_id),
            signature_id=str(signature.id),
            error=str(edge_exc),
        )

    counts["status"] = "extracted"
    counts["signature_key"] = key
    counts["is_new_signature"] = is_new

    if not is_new:
        counts["recurrence_links"] = await _link_recurrence(
            db, tenant_id, episode, signature.id
        )
    return counts


async def _link_recurrence(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    episode: Episode,
    signature_id: uuid.UUID,
) -> int:
    """C2: the new episode's seed evidence gains a recurrence membership
    to the most recent PRIOR episode's case. One membership — a pointer
    to precedent, never a merge (the cluster resolver excludes
    recurrence from expansion)."""
    if not episode.evidence_ids:
        return 0
    prior_link = (
        await db.execute(
            select(EpisodeIssueSignature.episode_id)
            .where(
                EpisodeIssueSignature.tenant_id == tenant_id,
                EpisodeIssueSignature.issue_signature_id == signature_id,
                EpisodeIssueSignature.episode_id != episode.id,
            )
            .order_by(EpisodeIssueSignature.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if prior_link is None:
        return 0
    prior_episode = await db.get(Episode, prior_link)
    if prior_episode is None or not prior_episode.evidence_ids:
        return 0
    prior_case = (
        await db.execute(
            select(EvidenceCaseMembership.canonical_case_id)
            .where(
                EvidenceCaseMembership.tenant_id == tenant_id,
                EvidenceCaseMembership.evidence_id.in_(
                    tuple(uuid.UUID(e) for e in prior_episode.evidence_ids[:50])
                ),
                EvidenceCaseMembership.relationship_type == "primary_case",
                EvidenceCaseMembership.status == "active",
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if prior_case is None:
        return 0
    seed_evidence = uuid.UUID(episode.evidence_ids[0])
    added = await _add_membership(
        db,
        tenant_id,
        seed_evidence,
        prior_case,
        "recurrence",
        RECURRENCE_CONFIDENCE,
        "issue_signature",
    )
    if added:
        logger.info(
            "issue_signature.recurrence_linked",
            tenant_id=str(tenant_id),
            episode_id=str(episode.id),
            prior_episode_id=str(prior_link),
            prior_case_id=str(prior_case),
        )
    return 1 if added else 0
