"""Find the KB/SOP content relevant to a pattern, for playbook generation.

Until now the playbook generator saw only what engineers *did* — pattern
description, episode summaries, and negative knowledge. It never saw what
the organisation says *should* be done. So a playbook generated from VPN
incidents reflected observed practice and silently omitted whatever the
approved SOP required but nobody happened to perform.

The concrete failure that motivates this:

    SOP:       stop service → back up certificate → renew → restart
    Episodes:  engineer renewed the certificate and restarted
    Generated: renew → restart          (the backup step is gone)

That playbook is not wrong about what happened. It is wrong about what
should happen, and nothing in the pipeline could notice.

Two design commitments carried from the review:

**Manual incident→KB links are not a dependency.** Engineers search the
KB and copy a command without recording the article, or work from
memory, or paste half an SOP into chat. Relationships are *discovered*
here — from the pattern's own vocabulary — and a manual link, when it
exists, is one high-confidence signal among several rather than the
mechanism.

**Knowledge is retrieved, never clustered.** A KB article is not evidence
that an incident occurred; it is what should be done about one. It is
fetched alongside the episode cluster and kept separate from it, so it
can inform a playbook without ever being mistaken for a record of
events.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.models.evidence import EvidenceChunk, EvidenceItem
from contextedge.services.evidence_typing import KNOWLEDGE_EVIDENCE_TYPES

logger = structlog.get_logger()

# How many knowledge documents reach the generator. Bounded because each
# one costs prompt tokens on an already-large call, and because a
# generator handed twenty loosely-related articles produces a playbook
# that cites all of them and follows none.
MAX_KNOWLEDGE_DOCS = 5
# Sections per document. A long SOP has many; the ones that matter for a
# procedure are the ones the pattern's own language matched.
MAX_SECTIONS_PER_DOC = 6
# Cosine distance ceiling. Beyond this the article shares vocabulary with
# the pattern but not subject matter — "VPN" appearing in an unrelated
# onboarding checklist. A weak match is worse than none: it gives the
# generator normative-sounding text about the wrong procedure.
MAX_DISTANCE = 0.55


@dataclass(slots=True)
class KnowledgeSection:
    """One citable section of a knowledge document."""

    text: str
    section_ref: str | None = None
    page: int | None = None
    chunk_kind: str = "heading_section"
    distance: float = 1.0
    # True when any part of this section came from a model reading an
    # image rather than from parsed text. Surfaced to the generator so a
    # paraphrase is never presented as the SOP's exact wording.
    model_derived: bool = False


@dataclass(slots=True)
class KnowledgeDocument:
    evidence_id: uuid.UUID
    title: str
    evidence_type: str
    sections: list[KnowledgeSection] = field(default_factory=list)
    best_distance: float = 1.0

    def to_prompt_block(self, index: int) -> str:
        lines = [f"[kb-{index}] {self.title} ({self.evidence_type})"]
        for section in self.sections:
            location = section.section_ref or "—"
            marker = " (read from an image)" if section.model_derived else ""
            lines.append(f"  § {location}{marker}: {section.text.strip()[:800]}")
        return "\n".join(lines)


def build_retrieval_query(
    *,
    pattern_title: str,
    pattern_description: str | None,
    episode_summaries: list[dict] | None = None,
) -> str:
    """Compose the text used to find relevant knowledge.

    Built from the pattern *and* its episodes' root causes rather than
    the pattern title alone. The review's point holds: an incident titled
    "Laptop Wi-Fi not working" retrieves almost nothing useful, while the
    same episode's established facts — the adapter, the error code, the
    action that worked — retrieve the article that documents them. The
    richer fingerprint is only available after episodes are reconstructed,
    which is why this runs at pattern time and not at ingest.
    """
    parts: list[str] = [pattern_title or ""]
    if pattern_description:
        parts.append(pattern_description)
    for episode in (episode_summaries or [])[:5]:
        for key in ("root_cause", "title", "outcome"):
            value = episode.get(key)
            if isinstance(value, str) and value.strip():
                parts.append(value.strip())
    return "\n".join(p for p in parts if p.strip())[:4000]


async def retrieve_knowledge_for_pattern(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    pattern_title: str,
    pattern_description: str | None = None,
    episode_summaries: list[dict] | None = None,
    limit: int = MAX_KNOWLEDGE_DOCS,
) -> list[KnowledgeDocument]:
    """Knowledge documents matching a pattern, best first.

    Returns ``[]`` rather than raising on any failure: generation without
    knowledge is the behaviour that shipped for months, so a retrieval
    problem must degrade to that rather than block playbook creation.
    """
    query = build_retrieval_query(
        pattern_title=pattern_title,
        pattern_description=pattern_description,
        episode_summaries=episode_summaries,
    )
    if not query.strip():
        return []

    try:
        return await _retrieve(db, tenant_id, query, limit)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "knowledge_retrieval.failed",
            tenant_id=str(tenant_id),
            error_type=type(exc).__name__,
        )
        return []


async def _retrieve(
    db: AsyncSession, tenant_id: uuid.UUID, query: str, limit: int
) -> list[KnowledgeDocument]:
    from contextedge.search.vector_search import search_evidence_semantic

    # Oversampled: the semantic search is not knowledge-aware, so the
    # filter below discards tickets and chat. Without the oversample a
    # pattern whose nearest neighbours are all incidents would return no
    # knowledge at all — which is exactly the case where the SOP matters.
    rows = await search_evidence_semantic(
        db, tenant_id, query, limit=max(limit * 6, 30)
    )

    documents: list[KnowledgeDocument] = []
    for row in rows:
        evidence = row[0]
        distance = float(row[1]) if len(row) > 1 and row[1] is not None else 1.0
        if getattr(evidence, "evidence_type", None) not in KNOWLEDGE_EVIDENCE_TYPES:
            continue
        if distance > MAX_DISTANCE:
            continue
        documents.append(
            KnowledgeDocument(
                evidence_id=evidence.id,
                title=(evidence.title or "Untitled")[:300],
                evidence_type=evidence.evidence_type,
                best_distance=distance,
            )
        )
        if len(documents) >= limit:
            break

    if not documents:
        return []

    await _attach_sections(db, tenant_id, documents, query)
    return documents


async def _attach_sections(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    documents: list[KnowledgeDocument],
    query: str,
) -> None:
    """Fill each document with its most relevant sections.

    Section-level rather than document-level because a 60-page SOP has
    one paragraph that answers the question and fifty that do not, and
    handing the generator the whole document buries the useful part.
    Chunk metadata carries the page and section path (phase 4c), so each
    section arrives already citable.
    """
    from contextedge.ai.provider import generate_embedding

    try:
        embedding = await generate_embedding(query, tenant_id=tenant_id, db=db)
    except Exception:  # noqa: BLE001
        embedding = None

    for document in documents:
        stmt = select(EvidenceChunk).where(
            EvidenceChunk.tenant_id == tenant_id,
            EvidenceChunk.evidence_id == document.evidence_id,
        )
        if embedding is not None:
            from contextedge.search.vector_ops import halfvec_cosine_distance

            distance = halfvec_cosine_distance(EvidenceChunk.embedding, embedding)
            stmt = stmt.where(EvidenceChunk.embedding.is_not(None)).order_by(distance)
        else:
            stmt = stmt.order_by(EvidenceChunk.chunk_index)

        chunks = (await db.execute(stmt.limit(MAX_SECTIONS_PER_DOC))).scalars().all()

        if not chunks:
            # No chunks (short document, or chunking failed). The body is
            # still the knowledge — better a whole-document section than
            # a document that silently contributes nothing.
            body = await db.get(EvidenceItem, document.evidence_id)
            if body is not None and (body.body_text or "").strip():
                document.sections = [
                    KnowledgeSection(text=body.body_text[:2000], section_ref=None)
                ]
            continue

        document.sections = [
            KnowledgeSection(
                text=chunk.text,
                section_ref=chunk.parent_section,
                page=(chunk.chunk_metadata or {}).get("page"),
                chunk_kind=chunk.chunk_kind,
                model_derived=_is_model_derived(chunk),
            )
            for chunk in chunks
        ]


def _is_model_derived(chunk: Any) -> bool:
    """Whether a chunk contains vision-read content.

    A section a model read out of a screenshot is a paraphrase, not the
    SOP's exact wording. The generator is told, so a step citing it can
    be reviewed accordingly rather than treated as a verbatim quotation
    of an approved procedure.
    """
    metadata = getattr(chunk, "chunk_metadata", None)
    if not isinstance(metadata, dict):
        metadata = {}
    methods = metadata.get("extraction_methods") or []
    return "vision" in methods


def format_knowledge_block(documents: list[KnowledgeDocument]) -> str:
    """Prompt rendering. ``"None found"`` when empty — an explicit
    absence, so the model does not invent normative sources to fill a
    silent gap."""
    if not documents:
        return "None found. Base the playbook on observed practice only."
    return "\n\n".join(
        document.to_prompt_block(index + 1) for index, document in enumerate(documents)
    )
