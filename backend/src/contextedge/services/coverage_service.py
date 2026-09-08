"""Coverage: what ContextEdge knows, and what it does not know it does not know.

Roadmap H2. The contract is that ContextEdge reports what it holds *and what
it cannot hold*, because an agent that cannot tell those apart reasons as
though absence of data were absence of events.

Concretely: an incident has no related change. There are five different worlds
behind that one empty result, and they call for five different next moves.

``not_configured`` No source is connected at all. A cold-start tenant.
``unsupported``    Nothing connected here can supply changes. Zoho Desk is a
                   help desk; it has no change management. Reporting "no
                   changes" is true and useless -- the honest answer is that
                   the question cannot be asked of this deployment.
``unavailable``    The connector supports changes, but the connected
                   *instance* does not expose them. ServiceNow's em_alert is
                   the live case: ITOM is not activated, the Table API answers
                   400, discovery steps over it. Distinct from not_selected
                   because there is no box to tick -- a module has to be
                   installed.
``not_selected``   The instance exposes it and nobody approved it for sync.
                   A ten-second fix nobody makes unless it is said aloud.
``pending``        Approved, but no sync has succeeded yet. Zero here means
                   "not fetched", not "none exist".
``empty``          A capable source is syncing changes and there are none. The
                   only status that is genuinely a finding.
``stale``          Rows exist, but the last successful sync is old enough that
                   "no recent change" may mean "no recent sync".

Every facet answers with one of those, plus what it counted, plus which
sources contributed. The distinctions cost nothing to compute and are the
difference between an agent saying "no change caused this" and "I cannot see
changes here". Each status also implies a different next move, which is the
test for whether it earns its place: connect a source / install a module /
tick a box / wait / believe the zero / re-sync first / read the number.

Facets are the unit deliberately. A single overall "coverage: 60%" number
would be worse than nothing -- it averages away the one dimension that matters
for the question actually being asked, and the missing dimension is never the
same one twice.

**Scope is tenant-wide, not domain-scoped**, unlike most read paths which
honour ``allowed_domain_ids``. That is deliberate: coverage answers "what can
this deployment see at all", which is a property of the connectors, not of the
records a given reader may read. Domain-scoping it would give two agents
different blind spots for the same instrumentation, and a blind spot that
varies by who is asking is not a blind spot -- it is a permissions artefact
that reads like one. The consequence is that the ``count`` figures are
tenant-wide totals, so a domain-limited reader sees counts larger than the
records they could retrieve. Counts here are evidence that a facet is
populated, never a result set.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.models.entity import Entity
from contextedge.models.evidence import EvidenceItem
from contextedge.models.pattern import GraphEdge
from contextedge.models.source import Source, SourceObject
from contextedge.services.source_capabilities import (
    capability_for,
    object_types_for,
    record_kinds_for,
)

# A facet backed by data whose newest successful sync is older than this is
# reported stale rather than available. Seven days is the CMDB topology TTL,
# which is the shortest freshness contract anything in the graph carries --
# using the same number keeps one idea with one value.
STALE_AFTER = timedelta(days=7)

# Dependency edge types. `contains` is composition, not dependency, and is
# excluded for the same reason change-risk excludes it: a rack containing a
# switch tells you nothing about what fails when the switch does.
TOPOLOGY_EDGE_TYPES = ("depends_on", "runs_on", "hosted_on", "uses", "connected_to")


@dataclass(frozen=True)
class FacetCoverage:
    """One dimension of what is knowable here."""

    facet: str
    status: str
    count: int = 0
    sources: tuple[str, ...] = ()
    last_synced_at: datetime | None = None
    detail: str = ""

    @property
    def is_answerable(self) -> bool:
        """Whether a question about this facet can be asked at all.

        ``empty`` is answerable -- the answer is "none". ``unsupported`` is
        not, and the difference is the whole point of this module.
        """
        return self.status in ("available", "stale", "empty")

    def as_dict(self) -> dict[str, Any]:
        return {
            "facet": self.facet,
            "status": self.status,
            "count": self.count,
            "sources": list(self.sources),
            "last_synced_at": (
                self.last_synced_at.isoformat() if self.last_synced_at else None
            ),
            "answerable": self.is_answerable,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class CoverageReport:
    facets: tuple[FacetCoverage, ...] = ()
    generated_at: datetime | None = None

    def by_facet(self, name: str) -> FacetCoverage | None:
        for f in self.facets:
            if f.facet == name:
                return f
        return None

    @property
    def blind_spots(self) -> tuple[str, ...]:
        """Facets no connected source can answer. This is the list an agent
        should read before concluding anything from an empty result."""
        return tuple(f.facet for f in self.facets if not f.is_answerable)

    def as_dict(self) -> dict[str, Any]:
        return {
            "generated_at": (
                self.generated_at.isoformat() if self.generated_at else None
            ),
            "facets": [f.as_dict() for f in self.facets],
            "blind_spots": list(self.blind_spots),
        }


# facet -> the canonical evidence types that back it. A facet backed by
# record kinds is answerable exactly when some connected source can produce
# one of them.
_RECORD_FACETS: dict[str, tuple[str, ...]] = {
    "incidents": ("incident", "ticket"),
    "changes": ("change",),
    "problems": ("problem",),
    "knowledge": ("kb_article", "sop", "documentation"),
    "requests": ("service_request", "task"),
    "monitoring": ("alert",),
}


async def _connected_sources(
    db: AsyncSession, tenant_id: uuid.UUID
) -> Sequence[Source]:
    """Sources that could actually deliver something.

    Inactive or unauthenticated sources are excluded deliberately: a source
    whose credentials expired supplies nothing, and counting it as coverage is
    how a silent sync failure reads as "no changes occurred".
    """
    rows = await db.execute(
        select(Source).where(
            Source.tenant_id == tenant_id,
            Source.is_active.is_(True),
            Source.auth_status == "connected",
        )
    )
    return list(rows.scalars().all())


@dataclass(frozen=True)
class _SyncState:
    """What discovery and sync have actually done for a set of object types.

    ``discovered`` is the one that matters most and is the easiest to skip.
    Discovery writes a SourceObject per object type the *instance* exposes,
    so its absence means the instance does not have that table -- not that an
    operator forgot to tick a box. ServiceNow's ``em_alert`` is the live case:
    the connector defines it, ITOM is not activated, the Table API answers
    400, discovery steps over it. Reporting that as "not approved for sync"
    sends someone to a checkbox that does not exist.
    """

    discovered: bool = False
    selected: bool = False
    ever_synced: bool = False
    last_sync: datetime | None = None


async def _any_source_object(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    source_ids: Sequence[uuid.UUID],
    external_ids: Sequence[str] | None = None,
) -> _SyncState:
    """Sync state across a source's objects, optionally narrowed by name."""
    if not source_ids:
        return _SyncState()
    where = [
        SourceObject.tenant_id == tenant_id,
        SourceObject.source_id.in_(list(source_ids)),
    ]
    if external_ids is not None:
        if not external_ids:
            return _SyncState()
        where.append(SourceObject.external_id.in_(list(external_ids)))
    rows = await db.execute(
        select(
            func.count(SourceObject.id),
            func.count(SourceObject.id).filter(
                SourceObject.approved_for_sync.is_(True)
            ),
            func.count(SourceObject.last_successful_sync_at),
            func.max(SourceObject.last_successful_sync_at),
        ).where(*where)
    )
    discovered, selected, synced, last_sync = rows.one()
    return _SyncState(
        discovered=bool(discovered),
        selected=bool(selected),
        ever_synced=bool(synced),
        last_sync=last_sync,
    )


async def _facet_sync_state(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    capable: Sequence[Source],
    evidence_types: Sequence[str],
) -> _SyncState:
    """Sync state for the objects that would supply this facet.

    Only ServiceNow names its source objects after its object types -- its
    discovery writes one SourceObject per *table*, so ``external_id`` is
    literally ``incident`` or ``change_request``. Every other connector names
    them after the thing being synced: Teams uses ``team:channel``, Gmail a
    mailbox address, Zoho ``tickets:<department>``, Jira a project key.

    Narrowing by object type is therefore precise on ServiceNow and matches
    nothing anywhere else -- which would report every facet on a Teams or Jira
    deployment as ``unavailable``, a confidently wrong blind spot. That is the
    exact error this module exists to prevent, so the narrowing is applied only
    where it can mean something: when the connector's object vocabulary is
    visible in its source-object names at all.

    Where it is not, the facet falls back to source-level sync state. Less
    precise -- it cannot distinguish "this channel is not synced" -- but it
    never invents an absence.
    """
    if not capable:
        return _SyncState()
    source_ids = [s.id for s in capable]

    needed: set[str] = set()
    vocabulary: set[str] = set()
    for source in capable:
        for evidence_type in evidence_types:
            needed |= object_types_for(source.source_type, evidence_type)
        for known in record_kinds_for(source.source_type):
            vocabulary |= object_types_for(source.source_type, known)

    addressable = await _any_source_object(
        db, tenant_id, source_ids, sorted(vocabulary)
    )
    if not addressable.discovered:
        return await _any_source_object(db, tenant_id, source_ids)
    return await _any_source_object(db, tenant_id, source_ids, sorted(needed))


def _status_from(
    *,
    any_source: bool,
    capable: Sequence[Source],
    sync: _SyncState,
    count: int,
    now: datetime,
) -> tuple[str, str]:
    """The whole decision, in one place so every facet answers the same way.

    Ordered from "cannot be asked" to "asked and answered". Each status implies
    a different next move, which is the test for whether a distinction earns
    its place: nothing / connect a source / activate a plugin / tick a box /
    wait for the sync / believe the zero / re-sync first / read the number.
    """
    if not any_source:
        return "not_configured", "No source is connected for this tenant."
    if not capable:
        return (
            "unsupported",
            "No connected source can supply this. An empty result here means "
            "the question cannot be asked, not that the answer is none.",
        )
    names = ", ".join(sorted({s.source_type for s in capable}))
    if not sync.discovered:
        return (
            "unavailable",
            f"{names} supports this, but the connected instance does not "
            f"expose it -- discovery found no such object. Usually an "
            f"uninstalled module rather than a setting.",
        )
    if not sync.selected:
        return (
            "not_selected",
            f"{names} exposes this and it is not approved for sync. A "
            f"configuration gap, not a finding.",
        )
    if not sync.ever_synced:
        return (
            "pending",
            f"{names} is approved for this but no sync has succeeded yet, so "
            f"zero means 'not fetched', not 'none exist'.",
        )
    if count == 0:
        return "empty", f"{names} is syncing this and has produced nothing."
    if sync.last_sync is not None and (now - sync.last_sync) > STALE_AFTER:
        age = now - sync.last_sync
        return (
            "stale",
            f"{count} record(s), but the last successful sync was {age.days} "
            f"days ago -- absence of recent data may be absence of recent sync.",
        )
    return "available", f"{count} record(s) from {names}."


async def _record_facet(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    facet: str,
    evidence_types: Sequence[str],
    sources: Sequence[Source],
    now: datetime,
) -> FacetCoverage:
    capable = [
        s
        for s in sources
        if record_kinds_for(s.source_type) & set(evidence_types)
    ]
    capable_ids = [s.id for s in capable]

    sync = await _facet_sync_state(db, tenant_id, capable, evidence_types)

    count = 0
    if capable_ids:
        count = int(
            (
                await db.execute(
                    select(func.count(EvidenceItem.id)).where(
                        EvidenceItem.tenant_id == tenant_id,
                        EvidenceItem.evidence_type.in_(list(evidence_types)),
                    )
                )
            ).scalar_one()
            or 0
        )

    status, detail = _status_from(
        any_source=bool(sources),
        capable=capable,
        sync=sync,
        count=count,
        now=now,
    )
    return FacetCoverage(
        facet=facet,
        status=status,
        count=count,
        sources=tuple(sorted({s.source_type for s in capable})),
        last_synced_at=sync.last_sync,
        detail=detail,
    )


async def _topology_facet(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    sources: Sequence[Source],
    now: datetime,
) -> FacetCoverage:
    """Topology is not backed by a record kind.

    CI-to-CI dependency edges come from a CMDB the connector queries directly,
    not from any record's reference fields, so this facet asks the capability
    declaration rather than the evidence-type map. Freshness comes from the
    entities' own ``last_synced_at``, because the topology cache has its own
    TTL and can be stale while ticket sync is current.
    """
    capable = [s for s in sources if capability_for(s.source_type).topology]

    count = int(
        (
            await db.execute(
                select(func.count(GraphEdge.id)).where(
                    GraphEdge.tenant_id == tenant_id,
                    GraphEdge.edge_type.in_(list(TOPOLOGY_EDGE_TYPES)),
                    GraphEdge.valid_to.is_(None),
                )
            )
        ).scalar_one()
        or 0
    )
    last_sync = (
        await db.execute(
            select(func.max(Entity.last_synced_at)).where(
                Entity.tenant_id == tenant_id
            )
        )
    ).scalar_one_or_none()

    status, detail = _status_from(
        any_source=bool(sources),
        capable=capable,
        # Topology is fetched on demand -- not approved per object and not
        # written by discovery -- so the configuration steps cannot fail for
        # it: a capable source can always warm it. Passing anything else would
        # report a permanent not_selected for a facet with no box to tick, and
        # an unwarmed cache is genuinely `empty` rather than `pending`.
        sync=_SyncState(
            discovered=True, selected=True, ever_synced=True, last_sync=last_sync
        ),
        count=count,
        now=now,
    )
    if status == "unsupported" and sources:
        detail = (
            "No connected source exposes a CMDB. Blast radius is unanswerable "
            "here -- dependants cannot be walked, only same-CI matches found."
        )
    return FacetCoverage(
        facet="topology",
        status=status,
        count=count,
        sources=tuple(sorted({s.source_type for s in capable})),
        last_synced_at=last_sync,
        detail=detail,
    )


async def _relation_facet(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    facet: str,
    relations: Sequence[str],
    sources: Sequence[Source],
    now: datetime,
    unsupported_detail: str,
) -> FacetCoverage:
    """A facet backed by graph relations rather than by records.

    'Are incidents linked to the change that caused them' is a different
    question from 'are there changes': a deployment can hold both incidents
    and changes and still have no connector able to state a causal link
    between them.
    """
    capable = [
        s
        for s in sources
        if capability_for(s.source_type).relations & set(relations)
    ]
    count = 0
    if capable:
        count = int(
            (
                await db.execute(
                    select(func.count(GraphEdge.id)).where(
                        GraphEdge.tenant_id == tenant_id,
                        GraphEdge.edge_type.in_(list(relations)),
                        GraphEdge.valid_to.is_(None),
                    )
                )
            ).scalar_one()
            or 0
        )
    status, detail = _status_from(
        any_source=bool(sources),
        capable=capable,
        # Relations are emitted by the reference layer as records normalize;
        # like topology they have no discovery or approval step of their own.
        sync=_SyncState(discovered=True, selected=True, ever_synced=True),
        count=count,
        now=now,
    )
    if status == "unsupported" and sources:
        detail = unsupported_detail
    return FacetCoverage(
        facet=facet,
        status=status,
        count=count,
        sources=tuple(sorted({s.source_type for s in capable})),
        detail=detail,
    )


async def build_coverage_report(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    now: datetime | None = None,
) -> CoverageReport:
    """Every facet, for this tenant, as of now."""
    now = now or datetime.now(UTC)
    sources = await _connected_sources(db, tenant_id)

    facets: list[FacetCoverage] = []
    for facet, evidence_types in _RECORD_FACETS.items():
        facets.append(
            await _record_facet(db, tenant_id, facet, evidence_types, sources, now)
        )

    facets.append(await _topology_facet(db, tenant_id, sources, now))

    facets.append(
        await _relation_facet(
            db,
            tenant_id,
            "causal_links",
            ("caused_by_change", "remediated_by_change"),
            sources,
            now,
            unsupported_detail=(
                "No connected source records which change caused an incident. "
                "Any change/incident correlation here is inferred from timing "
                "and topology, never asserted by a human."
            ),
        )
    )
    facets.append(
        await _relation_facet(
            db,
            tenant_id,
            "duplicate_links",
            ("child_of_incident", "duplicate_of"),
            sources,
            now,
            unsupported_detail=(
                "No connected source records incident duplication, so ticket "
                "counts here are ticket counts, not distinct occurrences."
            ),
        )
    )
    facets.append(
        await _relation_facet(
            db,
            tenant_id,
            "ownership",
            ("assigned_to_group",),
            sources,
            now,
            unsupported_detail=(
                "No connected source records an owning team, so there is no "
                "escalation target to name."
            ),
        )
    )

    return CoverageReport(facets=tuple(facets), generated_at=now)
