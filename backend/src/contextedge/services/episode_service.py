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
            status="draft",
            extraction_confidence=float(ep_data.get("overall_confidence", 0.5)),
            root_cause_summary=root_cause,
            final_outcome=ep_data.get("final_outcome"),
            reviewer_state="pending_review",
            evidence_ids=[str(eid) for eid in episode_evidence_ids],
            cluster_fingerprint=cluster_fingerprint,
            entity_refs=entity_refs,
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

    return created_episodes
