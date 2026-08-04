"""Episode reconstruction service."""

import uuid

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.ai.extractors.episode_extractor import reconstruct_episode
from contextedge.ai.provider import generate_embedding
from contextedge.models.episode import Episode, EpisodeEvidenceLink, EpisodeStep
from contextedge.models.evidence import EvidenceItem

logger = structlog.get_logger()


async def _resolve_primary_case_ref(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    evidence_ids: list[uuid.UUID],
) -> str | None:
    """The ticket number this episode should be cited by.

    An episode without one can be named but never opened: an agent cites the
    title and the reader has nothing to look up. That is the difference
    between a claim someone can verify in a click and one they must take on
    trust.

    Which identifier wins, when the cited evidence spans several cases:

    1. Follow the episode's OWN evidence to its canonical cases
       (``case_links``), never the whole cluster — the extractor may split a
       mixed cluster, and each episode should carry its own ticket.
    2. Prefer the case that the most of that evidence points at. An episode
       reconstructed from six messages about INC0001 and one stray reference
       to INC0002 is about INC0001.
    3. Within the case, take the identifier the correlation layer already
       marked ``is_authoritative`` — that flag exists precisely to answer
       this, so this code defers to it rather than inventing a second rule.
    4. Ties break on the lowest identifier value, so the same inputs always
       produce the same answer.

    Returns None when the evidence has no linked case yet, which is normal:
    correlation may not have run. The field stays empty rather than being
    filled with a guess.
    """
    if not evidence_ids:
        return None

    from contextedge.models.case_bridge import CaseIdentifier
    from contextedge.models.session import CaseLink

    rows = (
        await db.execute(
            select(CaseLink.canonical_case_id)
            .where(
                CaseLink.tenant_id == tenant_id,
                CaseLink.evidence_id.in_(evidence_ids),
            )
        )
    ).scalars().all()
    if not rows:
        return None

    counts: dict[uuid.UUID, int] = {}
    for case_id in rows:
        counts[case_id] = counts.get(case_id, 0) + 1
    # Most-referenced case first; ties resolve on the id so the choice is
    # reproducible rather than dependent on row order.
    best_case = sorted(counts.items(), key=lambda kv: (-kv[1], str(kv[0])))[0][0]

    identifiers = (
        await db.execute(
            select(CaseIdentifier.display_value, CaseIdentifier.is_authoritative)
            .where(
                CaseIdentifier.tenant_id == tenant_id,
                CaseIdentifier.canonical_case_id == best_case,
            )
        )
    ).all()
    if not identifiers:
        return None

    authoritative = sorted(
        (value for value, is_auth in identifiers if is_auth and value)
    )
    if authoritative:
        return authoritative[0]
    fallback = sorted(value for value, _ in identifiers if value)
    return fallback[0] if fallback else None


def _merge_identity_refs(values: list[dict | None]) -> dict | None:
    merged: list[dict] = []
    seen: set[str] = set()
    for refs in values:
        identities = (refs or {}).get("identities")
        if not isinstance(identities, list):
            continue
        for item in identities:
            if not isinstance(item, dict):
                continue
            canonical_id = item.get("canonical_id")
            if not canonical_id or canonical_id in seen:
                continue
            seen.add(str(canonical_id))
            merged.append(item)
    if not merged:
        return None
    return {"identities": merged}


async def create_episodes_from_evidence(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    domain_id: uuid.UUID | None,
    evidence_items: list[dict],
    evidence_ids: list[uuid.UUID],
    cluster_fingerprint: str | None = None,
    cluster_reasons: dict[str, list[str]] | None = None,
) -> list[Episode]:
    """Run LLM extraction and create one or more episodes with steps.

    Evidence membership is PER EPISODE: when the extractor splits a
    mixed cluster, each episode gets only the evidence it cited
    (validated against the input — the model can never mint evidence).
    An episode whose citations are missing/invalid falls back to the
    full cluster with a logged flag, never silently."""
    try:
        extracted_episodes = await reconstruct_episode(
            evidence_items, tenant_id=tenant_id, db=db,
        )
    except Exception as exc:
        logger.warning(
            "episode_reconstruction_llm_failed",
            tenant_id=str(tenant_id),
            evidence_count=len(evidence_items),
            error=str(exc),
        )
        return []

    # Review F-06: an empty extractor response is valid JSON but
    # distinct from "LLM failed" — log it so ops can tell drift
    # ("extractor stopped finding episodes") from outages.
    if not extracted_episodes:
        logger.info(
            "episode_reconstruction_zero_result",
            tenant_id=str(tenant_id),
            evidence_count=len(evidence_items),
        )
        return []

    all_entity_rows = (
        await db.execute(
            select(EvidenceItem.id, EvidenceItem.canonical_entity_refs).where(
                EvidenceItem.id.in_(evidence_ids)
            )
        )
    ).all()
    entity_refs_by_evidence = {row[0]: row[1] for row in all_entity_rows}
    valid_id_strings = {str(eid) for eid in evidence_ids}

    created_episodes = []
    for ep_data in extracted_episodes:
        # Per-episode membership from the model's citations, validated
        # against the input cluster.
        cited = [
            ref for ref in (ep_data.get("evidence_refs") or []) if ref in valid_id_strings
        ]
        if cited:
            episode_evidence_ids = [uuid.UUID(ref) for ref in dict.fromkeys(cited)]
            membership_source = "model_attribution"
        else:
            episode_evidence_ids = list(evidence_ids)
            membership_source = "full_cluster_fallback"
            logger.info(
                "episode_membership_fallback",
                tenant_id=str(tenant_id),
                episode_title=ep_data.get("title"),
                cluster_size=len(evidence_ids),
            )
        entity_refs = _merge_identity_refs(
            [entity_refs_by_evidence.get(eid) for eid in episode_evidence_ids]
        )
        # Generate semantic embedding for the episode
        title = ep_data.get("title", "Untitled Episode")
        root_cause = ep_data.get("root_cause_summary", "")
        emb_text = f"{title}\n\n{root_cause}"
        try:
            # Review F-03: thread tenant_id + db so the embedding call lands
            # in /admin/cost and respects the per-tenant budget gate.
            embedding = await generate_embedding(
                emb_text, tenant_id=tenant_id, db=db,
            )
        except Exception:
            logger.warning("episode_embedding_failed", ep_title=title)
            embedding = None

        episode = Episode(
            tenant_id=tenant_id,
            domain_id=domain_id,
            title=title,
            primary_case_ref=await _resolve_primary_case_ref(
                db, tenant_id, episode_evidence_ids
            ),
            status="draft",
            extraction_confidence=float(ep_data.get("overall_confidence", 0.5)),
            root_cause_summary=root_cause,
            final_outcome=ep_data.get("final_outcome"),
            reviewer_state="pending_review",
            evidence_ids=[str(eid) for eid in episode_evidence_ids],
            cluster_fingerprint=cluster_fingerprint,
            entity_refs=entity_refs,
            contradictions=ep_data.get("contradictions") or None,
            embedding=embedding,
        )
        db.add(episode)
        await db.flush()

        # Normalized provenance (0037): one row per grounding evidence,
        # carrying WHY it is in the cluster (review renders this).
        reasons = cluster_reasons or {}
        for evidence_id in episode_evidence_ids:
            why = reasons.get(str(evidence_id))
            db.add(
                EpisodeEvidenceLink(
                    tenant_id=tenant_id,
                    episode_id=episode.id,
                    evidence_id=evidence_id,
                    link_reason=(
                        ",".join(why)[:120] if why else membership_source
                    ),
                )
            )

        for step_data in ep_data.get("steps", []):
            step = EpisodeStep(
                episode_id=episode.id,
                step_order=step_data.get("step_order", 0),
                step_type=step_data.get("step_type", "unknown"),
                text=step_data.get("text", ""),
                observation=step_data.get("observation"),
                result_state=step_data.get("result_state", "unknown"),
                failed_flag=step_data.get("failed_flag", False),
                successful_flag=step_data.get("successful_flag", False),
                extraction_confidence=float(step_data.get("confidence", 0.5)),
                evidence_refs=step_data.get("evidence_refs"),
            )
            db.add(step)

        await db.flush()
        await db.refresh(episode)
        created_episodes.append(episode)

        # Synthesis-quality signal (P4 item 6): the fraction of steps the
        # model could not ground in evidence is the day-1 measurable
        # proxy for unsupported claims. Logged per episode so drift in
        # grounding quality shows up in operational events, not anecdotes.
        steps = ep_data.get("steps", [])
        ungrounded = sum(1 for s in steps if not s.get("evidence_refs"))
        logger.info(
            "episode.synthesis_quality",
            tenant_id=str(tenant_id),
            episode_id=str(episode.id),
            steps_total=len(steps),
            steps_ungrounded=ungrounded,
            unsupported_step_rate=(
                round(ungrounded / len(steps), 3) if steps else None
            ),
            contradiction_count=len(ep_data.get("contradictions") or []),
        )

    return created_episodes
