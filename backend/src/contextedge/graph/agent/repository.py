"""SQLAlchemy repository for bounded agent graph traversal and hydration."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

import structlog
from sqlalchemy import case, func, or_, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from contextedge.graph.agent.contracts import (
    AgentGraphAccessScope,
    AgentGraphRequest,
    GraphEdgeRecord,
    GraphNodeRef,
    HydratedGraphNode,
    RankedGraphSeed,
)
from contextedge.graph.agent.hydrators import NODE_MODELS, hydrate_node, node_is_visible
from contextedge.graph.temporal import edge_valid_at
from contextedge.models.entity import Entity
from contextedge.models.episode import CanonicalIdentity, Episode, IdentityAlias
from contextedge.models.pattern import GraphEdge, Pattern
from contextedge.models.playbook import Playbook
from contextedge.search.access_control import resolve_excluded_access_policy_ids

logger = structlog.get_logger()

# Identifier-shaped tokens in free text: emails (the canonical identity
# alias form), anything with a digit, tokens joined by ./-/_ (MG22,
# INC0010427, vpn-gw-east-01, ORDERS_DB), or short ALL-CAPS names. These
# are the operational nouns worth exact-matching against entities and
# identity aliases.
_IDENTIFIER_TOKEN_RE = re.compile(
    r"\b(?:"
    r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"  # email
    r"|[A-Za-z]*\d[A-Za-z0-9._-]*"          # contains a digit
    r"|[A-Za-z0-9]+(?:[._-][A-Za-z0-9]+)+"  # dotted/dashed/underscored
    r"|[A-Z]{3,12}"                          # short ALL-CAPS name
    r")\b"
)
_IDENTIFIER_STOPWORDS = frozenset(
    {
        "AND", "THE", "NOT", "FOR", "ALL", "ARE", "WAS", "CAN", "HAS",
        "BUT", "YOU", "OUR", "WHY", "NOW", "GET", "DID", "FYI", "EOD",
        "ASAP",
    }
)
MAX_QUERY_IDENTIFIER_TOKENS = 8


def extract_identifier_tokens(query: str) -> list[str]:
    """Deterministic operational-identifier extraction from free text —
    no LLM call on the agent hot path. Tokens must contain a letter
    (a bare year or ticket count is noise, not an identifier)."""
    tokens: list[str] = []
    seen: set[str] = set()
    for match in _IDENTIFIER_TOKEN_RE.finditer(query):
        token = match.group(0).strip("._-")
        if len(token) < 3 or token.upper() in _IDENTIFIER_STOPWORDS:
            continue
        if not any(c.isalpha() for c in token):
            continue
        key = token.lower()
        if key in seen:
            continue
        seen.add(key)
        tokens.append(token)
        if len(tokens) >= MAX_QUERY_IDENTIFIER_TOKENS:
            break
    return tokens


def _is_caps_word_token(token: str) -> bool:
    """A plain ALL-CAPS word ("VPN", "HELP") is only trustworthy as an
    EXACT match — substring fallback on shouted conversation words seeds
    arbitrary entities."""
    return token.isalpha() and token.isupper()


class AgentGraphRepository(Protocol):
    async def resolve_seeds(
        self,
        request: AgentGraphRequest,
        scope: AgentGraphAccessScope,
    ) -> list[RankedGraphSeed]: ...

    async def load_edges(
        self,
        frontier: Sequence[GraphNodeRef],
        scope: AgentGraphAccessScope,
        as_of: datetime | None,
    ) -> list[GraphEdgeRecord]: ...

    async def hydrate_nodes(
        self,
        nodes: Sequence[GraphNodeRef],
        scope: AgentGraphAccessScope,
    ) -> dict[str, HydratedGraphNode]: ...


class SQLAlchemyAgentGraphRepository:
    # Per-frontier-node edge cap: a hub node (an entity referenced by tens of
    # thousands of sessions) must not turn one traversal hop into a
    # tens-of-thousands-row fetch. The selector re-ranks and budgets after
    # this, so keeping the strongest edges per node is lossless in practice.
    EDGES_PER_FRONTIER_NODE = 200
    # Absolute backstop per load_edges call regardless of frontier size.
    MAX_EDGES_PER_HOP = 5_000

    def __init__(self, db: AsyncSession):
        self.db = db

    def _domain_predicate(self, column, scope: AgentGraphAccessScope):
        if scope.domain_id is not None:
            return or_(column.is_(None), column == scope.domain_id)
        if scope.allowed_domain_ids is not None:
            return or_(column.is_(None), column.in_(scope.allowed_domain_ids))
        return None

    async def resolve_seeds(
        self,
        request: AgentGraphRequest,
        scope: AgentGraphAccessScope,
    ) -> list[RankedGraphSeed]:
        seeds = [
            RankedGraphSeed(ref=seed, relevance=1.0, reason="explicit")
            for seed in request.seeds
        ]
        if request.session_id is not None:
            seeds.append(
                RankedGraphSeed(
                    ref=GraphNodeRef(type="session", id=request.session_id),
                    relevance=1.0,
                    reason="session",
                )
            )

        query = request.query.strip()
        identifier_tokens = extract_identifier_tokens(query) if query else []
        if query:
            # Layer A — FTS over playbooks (search_tsvector, migration 0007)
            # and patterns (small table; on-the-fly tsvector). The tsquery is
            # OR-composed from identifier tokens plus recent meaningful
            # words, ranked by ts_rank: plainto_tsquery over the raw window
            # would AND every lexeme of a multi-message conversation, which
            # no playbook can ever satisfy.
            fts_terms = [t.lower() for t in identifier_tokens]
            fts_seen = set(fts_terms)
            for word in re.findall(r"[A-Za-z]{4,}", query[-400:])[-16:]:
                lowered = word.lower()
                if lowered not in fts_seen:
                    fts_seen.add(lowered)
                    fts_terms.append(lowered)
            tsquery = (
                func.websearch_to_tsquery("english", " OR ".join(fts_terms[:24]))
                if fts_terms
                else None
            )
        if query and tsquery is not None:
            playbook_q = (
                select(Playbook.id, func.ts_rank(Playbook.search_tsvector, tsquery))
                .where(
                    Playbook.tenant_id == scope.tenant_id,
                    Playbook.lifecycle_state == "approved",
                    Playbook.search_tsvector.op("@@")(tsquery),
                )
                .order_by(func.ts_rank(Playbook.search_tsvector, tsquery).desc())
                .limit(3)
            )
            pattern_tsvector = func.to_tsvector(
                "english",
                Pattern.title + " " + func.coalesce(Pattern.description, ""),
            )
            pattern_q = (
                select(Pattern.id)
                .where(
                    Pattern.tenant_id == scope.tenant_id,
                    Pattern.active_flag.is_(True),
                    pattern_tsvector.op("@@")(tsquery),
                )
                .limit(3)
            )
            playbook_domain = self._domain_predicate(Playbook.domain_id, scope)
            pattern_domain = self._domain_predicate(Pattern.domain_id, scope)
            if playbook_domain is not None:
                playbook_q = playbook_q.where(playbook_domain)
            if pattern_domain is not None:
                pattern_q = pattern_q.where(pattern_domain)
            for row in (await self.db.execute(playbook_q)).all():
                seeds.append(
                    RankedGraphSeed(
                        ref=GraphNodeRef(type="playbook", id=row[0]),
                        relevance=0.9,
                        reason="query_fts",
                    )
                )
            for row in (await self.db.execute(pattern_q)).scalars().all():
                seeds.append(
                    RankedGraphSeed(
                        ref=GraphNodeRef(type="pattern", id=row),
                        relevance=0.85,
                        reason="query_fts",
                    )
                )

            # Layer B — semantic: similar approved past episodes by embedding
            # (halfvec HNSW, migration 0032). Traversal then pulls in their
            # patterns, playbooks, and evidence through belongs_to/
            # derived_from edges. Fail-soft AND transaction-safe: the SQL
            # runs inside a SAVEPOINT, because a swallowed database error
            # would otherwise leave the whole session aborted and every
            # later query in this projection raising InFailedSQLTransaction.
            episode_rows: list = []
            playbook_rows: list = []
            try:
                from contextedge.ai.provider import generate_embedding
                from contextedge.search.vector_ops import (
                    halfvec_cosine_distance,
                    tune_ann_recall,
                )

                # Embed the TAIL of the window — the newest messages hold
                # the actual question; the provider already trimmed to the
                # last 4,000 chars, and [:2000] would keep the oldest half.
                query_embedding = await generate_embedding(
                    query[-2_000:], tenant_id=scope.tenant_id, db=self.db
                )
                async with self.db.begin_nested():
                    await tune_ann_recall(self.db)
                    distance = halfvec_cosine_distance(
                        Episode.embedding, query_embedding
                    ).label("distance")
                    episode_q = (
                        select(Episode.id, distance)
                        .where(
                            Episode.tenant_id == scope.tenant_id,
                            Episode.embedding.is_not(None),
                            # Mirrors the playbook layer's approved filter:
                            # embeddings are written pre-review, so without
                            # this the 3 ANN slots go to pending episodes
                            # that hydration then (correctly) drops.
                            Episode.reviewer_state == "approved",
                        )
                        .order_by(distance)
                        .limit(3)
                    )
                    episode_domain = self._domain_predicate(Episode.domain_id, scope)
                    if episode_domain is not None:
                        episode_q = episode_q.where(episode_domain)
                    episode_rows = list((await self.db.execute(episode_q)).all())

                    # Direct semantic playbook match (0035): the embedding
                    # text includes trigger conditions and step titles, so
                    # symptom-level queries ("users can't log in") can reach
                    # a playbook whose title never says those words — and it
                    # works on cold-start tenants with no episode history.
                    pb_distance = halfvec_cosine_distance(
                        Playbook.embedding, query_embedding
                    ).label("distance")
                    playbook_sem_q = (
                        select(Playbook.id, pb_distance)
                        .where(
                            Playbook.tenant_id == scope.tenant_id,
                            Playbook.lifecycle_state == "approved",
                            Playbook.embedding.is_not(None),
                        )
                        .order_by(pb_distance)
                        .limit(3)
                    )
                    pb_domain = self._domain_predicate(Playbook.domain_id, scope)
                    if pb_domain is not None:
                        playbook_sem_q = playbook_sem_q.where(pb_domain)
                    playbook_rows = list((await self.db.execute(playbook_sem_q)).all())
            except Exception as exc:
                logger.warning(
                    "agent_graph.semantic_seed_unavailable",
                    tenant_id=str(scope.tenant_id),
                    error_type=type(exc).__name__,
                    error=str(exc),
                )
            for episode_id, episode_distance in episode_rows:
                similarity = 1.0 - min(max(float(episode_distance), 0.0), 1.0)
                if similarity < 0.5:
                    continue  # unrelated history is noise, not context
                seeds.append(
                    RankedGraphSeed(
                        ref=GraphNodeRef(type="episode", id=episode_id),
                        relevance=round(0.6 + 0.3 * similarity, 4),
                        reason="query_semantic",
                    )
                )
            for playbook_id, playbook_distance in playbook_rows:
                similarity = 1.0 - min(max(float(playbook_distance), 0.0), 1.0)
                if similarity < 0.5:
                    continue
                seeds.append(
                    RankedGraphSeed(
                        ref=GraphNodeRef(type="playbook", id=playbook_id),
                        relevance=round(0.6 + 0.3 * similarity, 4),
                        reason="query_semantic",
                    )
                )

            # Layer C — operational identifiers extracted from the query
            # (MG22, INC0010427, jsmith@acme.com, vpn-gw-east-01) matched
            # exactly against entities and identity aliases. Plain ALL-CAPS
            # words get exact matching only — no substring fallback.
            explicit_lowered = {t.lower() for t in request.entities}
            for token in identifier_tokens:
                if token.lower() in explicit_lowered:
                    continue  # explicit entities are handled below
                await self._seed_entity_term(
                    seeds,
                    scope,
                    token,
                    reason="query_identifier",
                    allow_fallback=not _is_caps_word_token(token),
                )

        for term in request.entities[:10]:
            await self._seed_entity_term(seeds, scope, term, reason="entity")

        deduplicated: dict[str, RankedGraphSeed] = {}
        for seed in seeds:
            current = deduplicated.get(seed.ref.key)
            if current is None or seed.relevance > current.relevance:
                deduplicated[seed.ref.key] = seed
        return sorted(
            deduplicated.values(),
            key=lambda item: (-item.relevance, item.ref.type, str(item.ref.id)),
        )[:20]

    async def _seed_entity_term(
        self,
        seeds: list[RankedGraphSeed],
        scope: AgentGraphAccessScope,
        term: str,
        *,
        reason: str,
        allow_fallback: bool = True,
    ) -> None:
        """Seed entities/identities for one operational term.

        Exact matches first: entity external ids and names, then identity
        aliases through the 0033 lookup index (tenant_id + normalized_alias
        — the predicate must include tenant_id or the index's leading
        column is unbound and every lookup is a scan). An exact identifier
        hit (``vpn-gw-east-01``) is a far stronger signal than a substring;
        icontains runs only as a fallback, and only when *allow_fallback*
        (plain conversation words get exact matching only).
        """
        normalized = " ".join(term.strip().split()).lower()
        if not normalized:
            return

        exact_entity_q = (
            select(Entity.id)
            .where(
                Entity.tenant_id == scope.tenant_id,
                Entity.is_active.is_(True),
                or_(
                    func.lower(Entity.name) == normalized,
                    func.lower(Entity.external_id) == normalized,
                ),
            )
            .limit(3)
        )
        entity_domain = self._domain_predicate(Entity.domain_id, scope)
        if entity_domain is not None:
            exact_entity_q = exact_entity_q.where(entity_domain)
        exact_entities = list((await self.db.execute(exact_entity_q)).scalars().all())
        for row in exact_entities:
            seeds.append(
                RankedGraphSeed(
                    ref=GraphNodeRef(type="entity", id=row),
                    relevance=0.95,
                    reason=f"{reason}_exact",
                )
            )

        exact_identity_q = (
            select(IdentityAlias.canonical_identity_id)
            .join(
                CanonicalIdentity,
                CanonicalIdentity.id == IdentityAlias.canonical_identity_id,
            )
            .where(
                IdentityAlias.tenant_id == scope.tenant_id,
                CanonicalIdentity.tenant_id == scope.tenant_id,
                CanonicalIdentity.is_active.is_(True),
                CanonicalIdentity.resolution_state.in_(("resolved", "verified")),
                IdentityAlias.normalized_alias == normalized,
            )
            .limit(3)
        )
        exact_identities = list(
            (await self.db.execute(exact_identity_q)).scalars().all()
        )
        for row in exact_identities:
            seeds.append(
                RankedGraphSeed(
                    ref=GraphNodeRef(type="identity", id=row),
                    relevance=0.9,
                    reason=f"{reason}_exact",
                )
            )

        if exact_entities or exact_identities or not allow_fallback:
            return

        entity_q = (
            select(Entity.id)
            .where(
                Entity.tenant_id == scope.tenant_id,
                Entity.is_active.is_(True),
                Entity.name.icontains(term, autoescape=True),
            )
            .limit(3)
        )
        if entity_domain is not None:
            entity_q = entity_q.where(entity_domain)
        identity_q = (
            select(CanonicalIdentity.id)
            .where(
                CanonicalIdentity.tenant_id == scope.tenant_id,
                CanonicalIdentity.is_active.is_(True),
                CanonicalIdentity.resolution_state.in_(("resolved", "verified")),
                CanonicalIdentity.canonical_name.icontains(term, autoescape=True),
            )
            .limit(3)
        )
        for row in (await self.db.execute(entity_q)).scalars().all():
            seeds.append(
                RankedGraphSeed(
                    ref=GraphNodeRef(type="entity", id=row),
                    relevance=0.9,
                    reason=reason,
                )
            )
        for row in (await self.db.execute(identity_q)).scalars().all():
            seeds.append(
                RankedGraphSeed(
                    ref=GraphNodeRef(type="identity", id=row),
                    relevance=0.85,
                    reason=reason,
                )
            )

    async def load_edges(
        self,
        frontier: Sequence[GraphNodeRef],
        scope: AgentGraphAccessScope,
        as_of: datetime | None,
    ) -> list[GraphEdgeRecord]:
        if not frontier:
            return []
        pairs = [(node.type, node.id) for node in frontier]
        source_matches = tuple_(
            GraphEdge.source_node_type,
            GraphEdge.source_node_id,
        ).in_(pairs)
        target_matches = tuple_(
            GraphEdge.target_node_type,
            GraphEdge.target_node_id,
        ).in_(pairs)

        # Rank edges per matched frontier endpoint and keep only the
        # strongest EDGES_PER_FRONTIER_NODE of each, so one hub node cannot
        # make a hop unbounded. Edges matching on both endpoints partition
        # under their source key, which is fine — they are counted once.
        partition_key = case(
            (
                source_matches,
                func.concat(GraphEdge.source_node_type, ":", GraphEdge.source_node_id),
            ),
            else_=func.concat(GraphEdge.target_node_type, ":", GraphEdge.target_node_id),
        )
        rank = (
            func.row_number()
            .over(
                partition_by=partition_key,
                order_by=(GraphEdge.weight.desc(), GraphEdge.id),
            )
            .label("frontier_rank")
        )
        inner = select(GraphEdge, rank).where(
            GraphEdge.tenant_id == scope.tenant_id,
            edge_valid_at(as_of),
            or_(source_matches, target_matches),
        )
        domain_predicate = self._domain_predicate(GraphEdge.domain_id, scope)
        if domain_predicate is not None:
            inner = inner.where(domain_predicate)
        subq = inner.subquery()
        ranked_edge = aliased(GraphEdge, subq)
        query = (
            select(ranked_edge)
            .where(subq.c.frontier_rank <= self.EDGES_PER_FRONTIER_NODE)
            # Deterministic survivors when the absolute cap bites.
            .order_by(subq.c.weight.desc(), subq.c.id)
            .limit(self.MAX_EDGES_PER_HOP)
        )

        rows = (await self.db.execute(query)).scalars().all()
        return [
            GraphEdgeRecord(
                source=GraphNodeRef(type=row.source_node_type, id=row.source_node_id),
                target=GraphNodeRef(type=row.target_node_type, id=row.target_node_id),
                type=row.edge_type,
                weight=float(row.weight),
                confidence=float(row.confidence) if row.confidence is not None else None,
                metadata=row.metadata_extra or {},
            )
            for row in rows
        ]

    async def hydrate_nodes(
        self,
        nodes: Sequence[GraphNodeRef],
        scope: AgentGraphAccessScope,
    ) -> dict[str, HydratedGraphNode]:
        grouped: dict[str, list[GraphNodeRef]] = defaultdict(list)
        for node in nodes:
            if node.type in NODE_MODELS:
                grouped[node.type].append(node)

        excluded = await resolve_excluded_access_policy_ids(
            self.db,
            scope.tenant_id,
            list(scope.roles),
        )
        excluded_evidence_policy_ids = set(excluded or [])
        hydrated: dict[str, HydratedGraphNode] = {}

        for node_type, refs in grouped.items():
            model = NODE_MODELS[node_type]
            query = select(model).where(
                model.id.in_([ref.id for ref in refs]),
                model.tenant_id == scope.tenant_id,
            )
            rows = (await self.db.execute(query)).scalars().all()
            for row in rows:
                if not node_is_visible(
                    node_type,
                    row,
                    scope,
                    excluded_evidence_policy_ids,
                ):
                    continue
                node = hydrate_node(node_type, row)
                hydrated[node.ref.key] = node
        return hydrated
