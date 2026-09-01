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
from contextedge.quality.section_purpose import infer_section_purpose_from_chunk
from contextedge.services.evidence_typing import KNOWLEDGE_EVIDENCE_TYPES
from contextedge.services.knowledge_lifecycle import is_current

logger = structlog.get_logger()

# How many knowledge documents reach the generator. Bounded because each
# one costs prompt tokens on an already-large call, and because a
# generator handed twenty loosely-related articles produces a playbook
# that cites all of them and follows none.
MAX_KNOWLEDGE_DOCS = 5
# Sections per document. A long SOP has many; the ones that matter for a
# procedure are the ones the pattern's own language matched.
MAX_SECTIONS_PER_DOC = 6
# Fetch extra candidate chunks before choosing what reaches the prompt. A
# pure semantic top-N often returns overview/problem sections and misses the
# lower-ranked procedure, validation, prerequisite, or rollback sections that
# should drive a product-specific playbook.
SECTION_SELECTION_OVERSAMPLE = 3
# Minimum similarity to write a pattern -> document edge. See
# persist_knowledge_links for how this was measured and why it is much
# stricter than the threshold used to seed a projection.
KNOWLEDGE_LINK_MIN_SIMILARITY = 0.75
# Cosine distance ceiling. Beyond this the article shares vocabulary with
# the pattern but not subject matter — "VPN" appearing in an unrelated
# onboarding checklist. A weak match is worse than none: it gives the
# generator normative-sounding text about the wrong procedure.
#
# Derived from the link threshold rather than set independently, because the
# two were answering the same question at different values and the looser one
# lost: at the old 0.55 ceiling (similarity >= 0.45), a generated playbook for
# "Process Studio SSL connection reset" cited "able to edit the ae url
# username" (similarity 0.61) as a normative source in its steps — exactly
# the vocabulary-noise band the link measurement mapped at 0.62-0.69. What is
# too weak to assert as a graph edge is also too weak to write into a
# procedure a reviewer is asked to approve.
MAX_DISTANCE = round(1.0 - KNOWLEDGE_LINK_MIN_SIMILARITY, 2)
# Embedding distance is the fetch. Stored product version only re-ranks:
# a matching-version article should beat a slightly closer embedding of a
# different release. It must never drop the older article — that KB is
# often still the full procedure for this ticket, and the playbook has to
# say so rather than omit it.
VERSION_MATCH_RANK = 0.82
# How much of an article to scan for applicability facets.
#
# This was 6000 and that number was invented rather than measured. On the
# live corpus the SHORTEST article is 8.3k characters and the longest is
# 29k, so a 6k window ended before the end of every single document — and
# the version statements, which sit in the "Applies to" or environment
# section partway down, fell outside it on all of them. The facet
# reported 0% coverage and looked like a corpus that never states a
# version, when it was a window that never reached one.
#
# It is a plain regex scan over text already in memory, so the cost of
# being generous here is negligible next to being wrong.
APPLICABILITY_SCAN_CHARS = 40_000

# Empirical support re-ranks, exactly as applicability does — it never
# filters. A procedure with a failure history is often still the only guidance
# that exists, and dropping it leaves the reviewer with nothing and no
# indication anything was withheld.
#
# Multipliers on cosine distance, so below 1.0 promotes and above 1.0 demotes.
# ``unproven`` and an absent record are BOTH neutral, deliberately: silence is
# not failure. Most knowledge is never exercised, and treating "no runs" as a
# negative signal would demote the whole corpus on day one — the same
# principle knowledge_validation_service states and this is where it has to
# hold to mean anything.
#
# ``contested`` is the only demotion and it is mild (1.25 against a 0.25
# distance ceiling). A contested article is not wrong; it is inconsistent,
# which is a reason to read it with the conflict in view rather than a reason
# to bury it.
SUPPORT_RANK_FACTORS: dict[str, float] = {
    "proven": 0.80,
    "emerging": 0.92,
    "unproven": 1.0,
    "contested": 1.25,
}


# A superseded article is demoted, not dropped (F4b) — the same rule
# applicability and support follow. The successor is usually also a candidate
# for the same query, so a demotion is enough to reorder them; and when the
# successor does NOT match, the predecessor is still the only guidance that
# exists and hiding it would leave the reviewer with nothing.
#
# Heavier than `contested` (1.25): "this has been replaced" is a stronger
# statement about an article than "its run record is mixed", and it is a
# statement a human reviewed rather than a statistic.
SUPERSEDED_RANK_FACTOR = 1.6


def support_rank_factor(stored: Any) -> tuple[float, str | None]:
    """``(factor, support level)`` from the stored knowledge-support blob.

    Anything unrecognised — absent column, malformed payload, a support level
    from a future vocabulary — is neutral. A ranker that raised or guessed
    here would turn a data problem into a retrieval that silently returns the
    wrong articles.
    """
    if not isinstance(stored, dict):
        return 1.0, None
    support = stored.get("support")
    if not isinstance(support, str):
        return 1.0, None
    return SUPPORT_RANK_FACTORS.get(support, 1.0), support


@dataclass(slots=True)
class KnowledgeSection:
    """One citable section of a knowledge document."""

    text: str
    section_ref: str | None = None
    page: int | None = None
    chunk_kind: str = "heading_section"
    distance: float = 1.0
    purpose: str = "context"
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
    # How this article's stated environment lines up with the incident's.
    # Carried into the prompt so a step citing an article written for a
    # different release is reviewable as such.
    applicability_notes: list[str] = field(default_factory=list)
    applicability_verdict: str = "unknown"
    # Product version stamped on the KB row at ingest
    # (``source_facets.version`` / ``applicability.product_versions``), and
    # the ticket version this retrieval was matched against. Carried into
    # the prompt so a step that follows a different-release article says so.
    product_version: str | None = None
    ticket_version: str | None = None
    version_conflict: tuple[str, str] | None = None
    # Empirical support (F4): has this procedure ever actually worked? None
    # when it has never been computed, which ranks and reads as neutral.
    support: str | None = None
    # A reviewer accepted that something replaced this article (F4b).
    superseded: bool = False

    def to_prompt_block(self, index: int, product_label: str | None = None) -> str:
        """Render this document for the prompt.

        ``product_label`` comes from the tenant's ontology and is absent for a
        tenant that has not named its product. It used to be the literal string
        "AutomationEdge" in this file, which meant every tenant's generation
        prompt asserted a product they may not run — and the model, having no
        other product name in its context, repeated it into the steps.
        """
        product = f"{product_label} " if product_label else ""
        header = f"[kb-{index}] {self.title} ({self.evidence_type})"
        if self.superseded:
            # Surfaced, not hidden: the generator should be able to say a
            # procedure has been replaced rather than quote it as current.
            header += " — SUPERSEDED: a newer version of this document exists"
        if self.support == "contested":
            # Surfaced, not hidden: the generator should be able to say the
            # procedure is disputed rather than quote it as settled.
            header += " — SUPPORT WARNING: this procedure has a mixed run record"
        if self.version_conflict:
            kb_ver, ticket_ver = self.version_conflict
            header += (
                f" — PRODUCT VERSION MISMATCH: this KB is for {product}"
                f"{kb_ver}; the ticket is {ticket_ver}. Use the full procedure. "
                f"Every step that follows this article MUST say in the step text "
                f"that it is based on the {kb_ver} KB (ticket is {ticket_ver})."
            )
        elif self.product_version and self.ticket_version:
            header += (
                f" — PRODUCT VERSION: {product}{self.product_version} "
                f"(matches ticket {self.ticket_version})"
            )
        elif self.product_version:
            header += f" — PRODUCT VERSION: {product}{self.product_version}"
        elif self.ticket_version:
            header += (
                " — PRODUCT VERSION: not stated on this article "
                f"(ticket is {self.ticket_version}; treat as version-agnostic)"
            )
        if self.applicability_verdict == "mismatch" and not self.version_conflict:
            header += " — APPLICABILITY WARNING: " + "; ".join(
                self.applicability_notes
            )
        elif self.applicability_notes and not self.version_conflict:
            header += " — " + "; ".join(self.applicability_notes)
        lines = [
            header,
            "  Section labels (ACTION/PREREQUISITE/VALIDATION/ROLLBACK) are "
            "structure-derived, not keyword matches. Binding obligations come "
            "from the QUALITY CONTRACT block when supplied.",
        ]
        for section in self.sections:
            purpose = section.purpose.upper()
            location = section.section_ref or "—"
            marker = " (read from an image)" if section.model_derived else ""
            location = f"{purpose} {location}"
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
        for step in episode.get("steps") or []:
            text = step.get("text") if isinstance(step, dict) else step
            if isinstance(text, str) and text.strip():
                parts.append(text.strip())
        for snippet in episode.get("thread_solutions") or []:
            if isinstance(snippet, str) and snippet.strip():
                parts.append(snippet.strip())
    return "\n".join(p for p in parts if p.strip())[:4000]


def _fill_article_version_from_stored_facets(
    evidence,
    article,
    parse_version_spec,
    platform_key: str,
    platform_version,
) -> None:
    """Prefer the version ingest already wrote on the row.

    Playbook fetch must not re-parse titles or filter by a hardcoded
    version list. If applicability already has a platform version, keep
    it. Otherwise take ``source_facets.version`` that ingest stamped.
    """
    if platform_version(article):
        return
    facets = getattr(evidence, "source_facets", None)
    raw = facets.get("version") if isinstance(facets, dict) else None
    if not raw:
        return
    spec = parse_version_spec(str(raw), allow_major_only=True)
    if spec is None:
        return
    article.product_versions[platform_key] = spec.raw
    article.versions.add(spec.raw)


async def retrieve_knowledge_for_pattern(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    pattern_title: str,
    pattern_description: str | None = None,
    episode_summaries: list[dict] | None = None,
    limit: int = MAX_KNOWLEDGE_DOCS,
    custom_fields: dict | None = None,
    version_field: str | None = None,
    environment_field: str | None = None,
    ci_traits: dict | None = None,
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

    from contextedge.services.knowledge_applicability_service import (
        describe_target,
        tenant_environment_inventory,
        tenant_vocabulary,
    )

    # Both come from this tenant's own entity graph, so applicability
    # works for whatever they actually run rather than for one hardcoded
    # product, and against the release stored on the closed ticket rather
    # than a single tenant-wide "our version". Candidates themselves are
    # always embedding-nearest neighbours — version is a stored facet
    # used to rank and label, never a hardcoded fetch list.
    vocabulary = await tenant_vocabulary(db, tenant_id)
    inventory = await tenant_environment_inventory(db, tenant_id)

    target = describe_target(
        pattern_title=pattern_title,
        pattern_description=pattern_description,
        episode_summaries=episode_summaries,
        ci_traits=ci_traits,
        custom_fields=custom_fields,
        version_field=version_field,
        environment_field=environment_field,
        vocabulary=vocabulary,
        environment_inventory=inventory,
    )

    try:
        return await _retrieve(db, tenant_id, query, limit, target, vocabulary)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "knowledge_retrieval.failed",
            tenant_id=str(tenant_id),
            error_type=type(exc).__name__,
        )
        return []


async def _retrieve(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    query: str,
    limit: int,
    target=None,
    vocabulary: set[str] | None = None,
) -> list[KnowledgeDocument]:
    from contextedge.search.vector_search import search_evidence_semantic

    # Oversampled: the semantic search is not knowledge-aware, so the
    # filter below discards tickets and chat. Without the oversample a
    # pattern whose nearest neighbours are all incidents would return no
    # knowledge at all — which is exactly the case where the SOP matters.
    rows = await search_evidence_semantic(
        db, tenant_id, query, limit=max(limit * 6, 30)
    )

    from contextedge.services.knowledge_applicability_service import (
        PLATFORM_KEY,
        _platform_version,
        applicability_from_payload,
        compare,
        extract_applicability,
        parse_version_spec,
    )

    documents: list[KnowledgeDocument] = []
    # Counted, not silent: "no guidance exists" and "all of it is retired or
    # unapproved" are different answers, and only one of them is a knowledge
    # gap somebody should act on.
    withheld = 0
    for row in rows:
        evidence = row[0]
        distance = float(row[1]) if len(row) > 1 and row[1] is not None else 1.0
        if getattr(evidence, "evidence_type", None) not in KNOWLEDGE_EVIDENCE_TYPES:
            continue
        # The source system's own lifecycle: a draft nobody approved, an
        # article in review, or one a human retired is not guidance. Withheld
        # rather than demoted, unlike supersession — there a filename
        # heuristic guessed, here a person used their own system to say so.
        if not is_current(getattr(evidence, "knowledge_state", None)):
            withheld += 1
            continue
        if distance > MAX_DISTANCE:
            continue

        document = KnowledgeDocument(
            evidence_id=evidence.id,
            title=(evidence.title or "Untitled")[:300],
            evidence_type=evidence.evidence_type,
            best_distance=distance,
        )

        # F4: empirical support re-ranks before applicability, and both are
        # multiplicative on distance, so an article that is both proven and
        # applicable compounds. getattr for the same reason applicability
        # uses it — search may hand back a partial projection, and reading an
        # absent column must not cost the caller the whole result set.
        support_factor, support = support_rank_factor(
            getattr(evidence, "knowledge_support", None)
        )
        document.support = support
        distance *= support_factor
        document.best_distance = distance

        # Fetch is embedding similarity. Stored product version (ingest
        # stamped ``applicability`` / ``source_facets.version``) only
        # re-ranks and labels. An older KB that is still the full procedure
        # for this ticket must reach the generator with the version gap
        # named, not be dropped by a hardcoded version filter.
        if target is not None:
            try:
                stored = getattr(evidence, "applicability", None)
                if isinstance(stored, dict) and stored:
                    article = applicability_from_payload(stored)
                else:
                    body = getattr(evidence, "body_text", None) or ""
                    article = extract_applicability(
                        f"{evidence.title or ''}\n{body[:APPLICABILITY_SCAN_CHARS]}",
                        vocabulary,
                    )
                _fill_article_version_from_stored_facets(
                    evidence, article, parse_version_spec, PLATFORM_KEY, _platform_version
                )
                match = compare(article, target)
                kb_version = _platform_version(article)
                ticket_ver = _platform_version(target)
                if not ticket_ver and target.versions:
                    ticket_ver = sorted(target.versions)[-1]
                document.product_version = kb_version
                document.ticket_version = ticket_ver
                document.version_conflict = match.version_conflict
                if ticket_ver and kb_version and match.version_conflict is None:
                    distance *= VERSION_MATCH_RANK
                document.best_distance = distance * match.rank_penalty
                document.applicability_verdict = match.verdict
                document.applicability_notes = match.notes()
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "knowledge_retrieval.applicability_failed",
                    evidence_id=str(evidence.id),
                    error_type=type(exc).__name__,
                )

        documents.append(document)

    # F4b: demote anything a reviewer has accepted as superseded. Applied once
    # over the candidate set rather than per document, because it is one query
    # for all of them — and applied BEFORE the truncation below, so a
    # superseded article cannot hold a slot its replacement should have.
    await _apply_supersession(db, tenant_id, documents)

    if withheld:
        logger.info(
            "knowledge_retrieval.withheld_by_source_lifecycle",
            tenant_id=str(tenant_id),
            withheld=withheld,
            served=len(documents),
        )

    documents.sort(key=lambda d: d.best_distance)
    documents = documents[:limit]

    if not documents:
        return []

    await _attach_sections(db, tenant_id, documents, query)
    return documents


async def _apply_supersession(
    db: AsyncSession, tenant_id: uuid.UUID, documents: list[KnowledgeDocument]
) -> None:
    """Demote documents a reviewer accepted as replaced (F4b).

    Fail-soft: a supersession lookup that errors leaves the ranking exactly as
    it was. A ranking input must never cost the caller the result set — the
    same rule the applicability path follows.
    """
    if not documents:
        return
    try:
        from contextedge.services.knowledge_supersession_service import (
            superseded_evidence_ids,
        )

        superseded = await superseded_evidence_ids(
            db, tenant_id, [d.evidence_id for d in documents]
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "knowledge_retrieval.supersession_failed", error_type=type(exc).__name__
        )
        return

    for document in documents:
        if document.evidence_id in superseded:
            document.best_distance *= SUPERSEDED_RANK_FACTOR
            document.superseded = True


def _section_purpose(chunk: Any) -> str:
    return infer_section_purpose_from_chunk(chunk)


def _section_priority(chunk: Any) -> int:
    purpose = _section_purpose(chunk)
    return {
        "rollback": 5,
        "validation": 4,
        "prerequisite": 3,
        "action": 3,
        "context": 0,
    }.get(purpose, 0)


def _select_prompt_sections(chunks: list[Any]) -> list[Any]:
    """Choose the sections that should reach playbook generation.

    Keep the closest semantic hit for topical context, then promote procedural
    chunks from the oversampled set. Finally restore document order so a
    multi-step procedure reads naturally in the prompt.
    """
    if len(chunks) <= MAX_SECTIONS_PER_DOC:
        return chunks

    selected: list[Any] = [chunks[0]]
    seen = {id(chunks[0])}
    ranked = sorted(
        enumerate(chunks[1:], start=1),
        key=lambda item: (-_section_priority(item[1]), item[0]),
    )
    for _, chunk in ranked:
        if len(selected) >= MAX_SECTIONS_PER_DOC:
            break
        if id(chunk) in seen or _section_priority(chunk) <= 0:
            continue
        selected.append(chunk)
        seen.add(id(chunk))

    for chunk in chunks:
        if len(selected) >= MAX_SECTIONS_PER_DOC:
            break
        if id(chunk) in seen:
            continue
        selected.append(chunk)
        seen.add(id(chunk))

    return sorted(selected, key=lambda chunk: getattr(chunk, "chunk_index", 0))


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

        chunks = (
            await db.execute(
                stmt.limit(MAX_SECTIONS_PER_DOC * SECTION_SELECTION_OVERSAMPLE)
            )
        ).scalars().all()
        chunks = _select_prompt_sections(list(chunks))

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
                purpose=_section_purpose(chunk),
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


async def persist_knowledge_links(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    pattern_id: uuid.UUID,
    documents: list[KnowledgeDocument],
    *,
    domain_id: uuid.UUID | None = None,
) -> int:
    """Record pattern → document matches as graph edges.

    Retrieval discovers these relationships every time a playbook is
    generated, and until now discarded them the moment the prompt was built.
    That left documentation reachable only by a direct semantic match on the
    question: measured on a live graph, 17 of 18 KB articles had no edge to
    any pattern or playbook, so an agent that landed on the right pattern
    still could not traverse to the article documenting it.

    ``supported_by`` rather than a new edge type — the pattern's procedure is
    supported by the document, which is what the existing vocabulary already
    means, and it carries a 1.15 traversal weight in maf.v1 so a document two
    hops out survives the projection budget instead of decaying below the cut.

    Only confident, applicable matches are written. An edge is a durable
    claim that these two things belong together; a weak or
    wrong-release match would be asserted here and then read back as fact by
    every later projection. The applicability verdict already computed for
    the prompt is reused rather than recomputed.

    Idempotent via ``ensure_edge``. Never raises: a failure to record the
    relationship must not fail playbook generation, which is what this
    retrieval was actually called for.
    """
    if not documents:
        return 0

    from contextedge.graph.builder import ensure_edge

    written = 0
    for document in documents:
        # best_distance is a cosine distance, so smaller is closer.
        #
        # Deliberately far stricter than the 0.6 the seeding layer uses, and
        # the gap is the point: a seed is transient and competes on relevance,
        # so a weak one simply ranks low and falls out of the budget. An edge
        # is a durable assertion that these two things belong together, read
        # back as fact by every later projection and by anything that
        # traverses the graph. Wrong seeds cost a little context; wrong edges
        # corrupt the graph.
        #
        # 0.75 was measured, not chosen. Ranking every pattern in a live
        # tenant against its best-matching document put genuine pairs at
        # 0.75-0.84 ("ActiveMQ broker not running" -> "activemq services not
        # running"; "Agent fails to start after update" -> "agent is in
        # updating stage") and pure vocabulary noise at 0.62-0.69 ("Process
        # Studio SSL connection reset" -> "active directory error"; "VPN
        # gateway session limit" -> "agent controller issue"). At 0.6 every
        # one of those wrong pairs would have been written as a permanent
        # edge.
        similarity = 1.0 - min(max(float(document.best_distance), 0.0), 1.0)
        if similarity < KNOWLEDGE_LINK_MIN_SIMILARITY:
            continue
        if document.applicability_verdict == "mismatch":
            # Written for a different release or environment. Useful to show
            # a human with the warning attached, not to assert as a link.
            continue
        try:
            await ensure_edge(
                db,
                tenant_id,
                "pattern",
                pattern_id,
                "evidence",
                document.evidence_id,
                "supported_by",
                # Both, deliberately: similarity IS the belief in this
                # relationship (confidence), and a better-matched document
                # should also matter more in traversal (weight). Setting only
                # weight — as the first version of this code did — was the
                # exact weight-as-confidence conflation the graph review
                # flagged across writers.
                weight=round(similarity, 4),
                confidence=round(similarity, 4),
                metadata={
                    "source": "knowledge_retrieval",
                    "evidence_type": document.evidence_type,
                    "applicability": document.applicability_verdict,
                },
                domain_id=domain_id,
            )
            written += 1
        except Exception as exc:
            logger.warning(
                "knowledge_retrieval.link_failed",
                tenant_id=str(tenant_id),
                pattern_id=str(pattern_id),
                evidence_id=str(document.evidence_id),
                error=str(exc),
            )
    return written


def format_knowledge_block(
    documents: list[KnowledgeDocument], product_label: str | None = None
) -> str:
    """Prompt rendering. ``"None found"`` when empty — an explicit
    absence, so the model does not invent normative sources to fill a
    silent gap.

    ``product_label`` is the tenant's product name from its ontology; omitted,
    version text carries no product name, which is the honest rendering for a
    tenant that has not declared one."""
    if not documents:
        return "None found. Base the playbook on observed practice only."
    return "\n\n".join(
        document.to_prompt_block(index + 1, product_label)
        for index, document in enumerate(documents)
    )


def knowledge_refs_payload(
    documents: list[KnowledgeDocument],
    *,
    ticket_version: str | None = None,
) -> dict[str, Any]:
    """Provenance blob persisted on the generated playbook version.

    The generator sees these documents; the version must record the same
    set (and the ticket AE version used to filter them) so a reviewer
    can open the SOPs and see which release they were matched against.
    """
    payload: dict[str, Any] = {
        "knowledge_ids": [str(document.evidence_id) for document in documents],
        "knowledge": [
            {
                "evidence_id": str(document.evidence_id),
                "title": document.title,
                "evidence_type": document.evidence_type,
                "applicability_verdict": document.applicability_verdict,
                "applicability_notes": document.applicability_notes,
                "product_version": document.product_version,
                "version_mismatch": (
                    list(document.version_conflict)
                    if document.version_conflict
                    else None
                ),
            }
            for document in documents
        ],
    }
    if ticket_version:
        payload["ticket_version"] = ticket_version
    return payload
