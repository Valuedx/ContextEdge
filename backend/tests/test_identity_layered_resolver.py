"""Layered identity resolution: strong IDs -> typed alias -> adjudication -> provisional."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from contextedge.models.episode import CanonicalIdentity, IdentityAlias
from contextedge.services.identity_normalizer import (
    normalize_extracted_entity,
    normalize_text,
)
from contextedge.services.identity_service import (
    AdjudicationResult,
    resolve_extracted_entities,
)


class _FirstResult:
    def __init__(self, row):
        self._row = row

    def first(self):
        return self._row

    def scalars(self):
        return SimpleNamespace(all=lambda: [])


class _ScalarsResult:
    def __init__(self, values):
        self._values = values

    def scalars(self):
        return SimpleNamespace(all=lambda: list(self._values))

    def first(self):
        return None


def _db(execute_results):
    results = list(execute_results)
    added = []

    async def _execute(stmt):
        if results:
            return results.pop(0)
        return _FirstResult(None)

    db = SimpleNamespace(
        execute=AsyncMock(side_effect=_execute),
        add=lambda obj: added.append(obj),
        flush=AsyncMock(),
    )
    return db, added


# ---- normalizer ----


def test_normalizer_classifies_email_display_name_as_strong_identifier():
    entity = normalize_extracted_entity(
        {"entity_type": "person", "name": "jsmith@acme.com"}
    )
    assert ("email", "jsmith@acme.com", None) in entity.strong_identifiers


def test_normalizer_extracts_structured_identifiers():
    entity = normalize_extracted_entity(
        {
            "entity_type": "person",
            "display_name": "John Smith",
            "email": "JSmith@Acme.com",
            "username": "jsmith",
            "source_identifiers": {"teams_user_id": "29:abc123"},
        }
    )
    ids = entity.strong_identifiers
    assert ("email", "jsmith@acme.com", None) in ids
    assert ("username", "jsmith", None) in ids
    assert ("external_id", "29:abc123", "teams_user_id") in ids


def test_normalizer_single_token_device_name_is_hostname():
    entity = normalize_extracted_entity(
        {"entity_type": "device", "name": "vpn-gw-east-01"}
    )
    assert ("hostname", "vpn-gw-east-01", None) in entity.strong_identifiers


def test_normalizer_whitespace_and_case():
    assert normalize_text("  John   SMITH ") == "john smith"


# ---- layer 1: strong identifiers ----


@pytest.mark.asyncio
async def test_strong_identifier_match_wins_with_confidence_1():
    tenant_id = uuid4()
    canonical = CanonicalIdentity(
        id=uuid4(),
        tenant_id=tenant_id,
        entity_type="person",
        canonical_name="John Smith",
        resolution_state="resolved",
    )
    alias = IdentityAlias(
        id=uuid4(),
        canonical_identity_id=canonical.id,
        alias_text="jsmith@acme.com",
        alias_type="email",
        times_observed=1,
    )
    db, added = _db(
        [
            _FirstResult((alias, canonical)),  # strong lookup hit
            _FirstResult(None),  # _learn_alias existing check
        ]
    )

    with patch(
        "contextedge.services.identity_service.append_operational_event", AsyncMock()
    ):
        resolved = await resolve_extracted_entities(
            db,
            tenant_id,
            [
                {
                    "entity_type": "person",
                    "display_name": "J. Smith",
                    "email": "jsmith@acme.com",
                }
            ],
        )

    assert len(resolved) == 1
    assert resolved[0]["matched_via"] == "strong:email"
    assert resolved[0]["confidence"] == 1.0
    assert resolved[0]["canonical_id"] == canonical.id
    # The observed display name is learned as a new alias.
    learned = [o for o in added if isinstance(o, IdentityAlias)]
    assert any(a.normalized_alias == "j. smith" for a in learned)
    assert alias.times_observed == 2


# ---- layer 2: typed exact alias ----


@pytest.mark.asyncio
async def test_typed_alias_match_does_not_cross_entity_types():
    """The SQL for the alias lookup must filter on entity_type."""
    tenant_id = uuid4()
    captured = []

    async def _execute(stmt):
        captured.append(str(stmt))
        return _FirstResult(None)

    db = SimpleNamespace(
        execute=AsyncMock(side_effect=_execute),
        add=lambda obj: None,
        flush=AsyncMock(),
    )

    with (
        patch(
            "contextedge.services.identity_service._candidate_identities",
            AsyncMock(return_value=[]),
        ),
        patch(
            "contextedge.services.identity_service.append_operational_event",
            AsyncMock(),
        ),
    ):
        await resolve_extracted_entities(
            db, tenant_id, [{"entity_type": "application", "name": "Phoenix"}]
        )

    # The exact-alias query (first execute for a no-strong-ID entity) joins
    # canonical_identities and filters by entity_type.
    assert "entity_type" in captured[0]


@pytest.mark.asyncio
async def test_exact_alias_match_confidence():
    tenant_id = uuid4()
    canonical = CanonicalIdentity(
        id=uuid4(),
        tenant_id=tenant_id,
        entity_type="service",
        canonical_name="Acme VPN",
        resolution_state="resolved",
    )
    alias = IdentityAlias(
        id=uuid4(),
        canonical_identity_id=canonical.id,
        alias_text="Acme VPN",
        alias_type="display_name",
        times_observed=3,
    )
    db, _added = _db([_FirstResult((alias, canonical))])

    resolved = await resolve_extracted_entities(
        db, tenant_id, [{"entity_type": "service", "name": "acme vpn"}]
    )

    assert resolved[0]["matched_via"] == "alias_exact"
    assert resolved[0]["confidence"] == 0.95
    assert alias.times_observed == 4


# ---- layer 3: adjudication ----


@pytest.mark.asyncio
async def test_adjudicated_match_above_threshold_links():
    tenant_id = uuid4()
    candidate = CanonicalIdentity(
        id=uuid4(),
        tenant_id=tenant_id,
        entity_type="person",
        canonical_name="John Smith",
        resolution_state="resolved",
    )
    db, added = _db(
        [
            _FirstResult(None),  # exact alias miss
            _FirstResult(None),  # _learn_alias existing check
        ]
    )

    with (
        patch(
            "contextedge.services.identity_service._candidate_identities",
            AsyncMock(return_value=[candidate]),
        ),
        patch(
            "contextedge.services.identity_service._adjudicate_candidates",
            AsyncMock(
                return_value=AdjudicationResult(
                    decision="match",
                    candidate_id=str(candidate.id),
                    confidence=0.97,
                    reason="username matches existing alias",
                )
            ),
        ),
        patch(
            "contextedge.services.identity_service.append_operational_event",
            AsyncMock(),
        ),
    ):
        resolved = await resolve_extracted_entities(
            db, tenant_id, [{"entity_type": "person", "name": "J. Smith"}]
        )

    assert resolved[0]["matched_via"] == "llm_adjudicated"
    assert resolved[0]["canonical_id"] == candidate.id
    assert resolved[0]["confidence"] == 0.97


@pytest.mark.asyncio
async def test_adjudicated_match_below_person_threshold_goes_to_review():
    tenant_id = uuid4()
    candidate = CanonicalIdentity(
        id=uuid4(),
        tenant_id=tenant_id,
        entity_type="person",
        canonical_name="John Smith",
        resolution_state="resolved",
    )
    db, added = _db([_FirstResult(None)])  # exact alias miss

    with (
        patch(
            "contextedge.services.identity_service._candidate_identities",
            AsyncMock(return_value=[candidate]),
        ),
        patch(
            "contextedge.services.identity_service._adjudicate_candidates",
            AsyncMock(
                return_value=AdjudicationResult(
                    decision="match",
                    candidate_id=str(candidate.id),
                    confidence=0.91,  # below the 0.95 person threshold
                )
            ),
        ),
        patch(
            "contextedge.services.identity_service.append_operational_event",
            AsyncMock(),
        ),
    ):
        resolved = await resolve_extracted_entities(
            db, tenant_id, [{"entity_type": "person", "name": "J. Smith"}]
        )

    assert resolved[0]["matched_via"] == "needs_review"
    assert resolved[0]["canonical_id"] != candidate.id
    created = [o for o in added if isinstance(o, CanonicalIdentity)]
    assert created and created[0].resolution_state == "needs_review"


@pytest.mark.asyncio
async def test_abstention_creates_needs_review_identity():
    tenant_id = uuid4()
    candidate = CanonicalIdentity(
        id=uuid4(),
        tenant_id=tenant_id,
        entity_type="person",
        canonical_name="James Smith",
        resolution_state="resolved",
    )
    db, added = _db([_FirstResult(None)])

    with (
        patch(
            "contextedge.services.identity_service._candidate_identities",
            AsyncMock(return_value=[candidate]),
        ),
        patch(
            "contextedge.services.identity_service._adjudicate_candidates",
            AsyncMock(
                return_value=AdjudicationResult(
                    decision="needs_review", candidate_id=None, confidence=0.58
                )
            ),
        ),
        patch(
            "contextedge.services.identity_service.append_operational_event",
            AsyncMock(),
        ),
    ):
        resolved = await resolve_extracted_entities(
            db, tenant_id, [{"entity_type": "person", "name": "J. Smith"}]
        )

    created = [o for o in added if isinstance(o, CanonicalIdentity)]
    assert created[0].resolution_state == "needs_review"
    assert created[0].resolution_method == "adjudication_abstained"


# ---- layer 4: provisional creation ----


@pytest.mark.asyncio
async def test_miss_creates_provisional_not_trusted_identity():
    tenant_id = uuid4()
    db, added = _db([_FirstResult(None)])  # exact alias miss

    with (
        patch(
            "contextedge.services.identity_service._candidate_identities",
            AsyncMock(return_value=[]),
        ),
        patch(
            "contextedge.services.identity_service.append_operational_event",
            AsyncMock(),
        ),
    ):
        resolved = await resolve_extracted_entities(
            db, tenant_id, [{"entity_type": "person", "name": "Unknown Person"}]
        )

    assert resolved[0]["matched_via"] == "provisional_new"
    assert resolved[0]["confidence"] == 0.5
    created = [o for o in added if isinstance(o, CanonicalIdentity)]
    assert created[0].resolution_state == "provisional"
    assert created[0].resolution_method == "unmatched_new"
    aliases = [o for o in added if isinstance(o, IdentityAlias)]
    assert aliases and aliases[0].alias_type == "display_name"


# ---- snapshot rebuild helper ----


def test_rewrite_identity_refs_replaces_and_dedupes():
    from contextedge.workers.identity_tasks import _rewrite_identity_refs

    primary = SimpleNamespace(id=uuid4(), canonical_name="John Smith")
    duplicate_id = str(uuid4())
    refs = {
        "identities": [
            {"canonical_id": duplicate_id, "canonical_name": "J. Smith"},
            {"canonical_id": str(primary.id), "canonical_name": "John Smith"},
        ],
        "decisions": [{"id": "keep-me"}],
    }

    rewritten = _rewrite_identity_refs(refs, duplicate_id, primary)

    assert rewritten is not None
    identities = rewritten["identities"]
    assert len(identities) == 1
    assert identities[0]["canonical_id"] == str(primary.id)
    assert rewritten["decisions"] == [{"id": "keep-me"}]


def test_rewrite_identity_refs_no_change_returns_none():
    from contextedge.workers.identity_tasks import _rewrite_identity_refs

    primary = SimpleNamespace(id=uuid4(), canonical_name="John Smith")
    refs = {"identities": [{"canonical_id": str(uuid4()), "canonical_name": "Other"}]}

    assert _rewrite_identity_refs(refs, str(uuid4()), primary) is None


# ---- merge (first coverage of merge_canonical_identities) ----


@pytest.mark.asyncio
async def test_merge_repoints_aliases_and_records_duplicate_name():
    """The duplicate's name becomes a display alias on the primary with a
    STRING normalized_alias (regression: a tuple was passed as the column
    value, 500ing every merge), and the survivor is marked verified."""
    from contextedge.services.identity_service import merge_canonical_identities

    tenant_id = uuid4()
    primary = CanonicalIdentity(
        id=uuid4(), tenant_id=tenant_id, entity_type="person",
        canonical_name="John Smith", resolution_state="resolved",
        metadata_extra=None, is_active=True,
    )
    duplicate = CanonicalIdentity(
        id=uuid4(), tenant_id=tenant_id, entity_type="person",
        canonical_name="J. Smith", resolution_state="provisional",
        metadata_extra=None, is_active=True,
    )
    dup_email = IdentityAlias(
        id=uuid4(), canonical_identity_id=duplicate.id, tenant_id=tenant_id,
        alias_text="jsmith@acme.com", normalized_alias="jsmith@acme.com",
        alias_type="email", times_observed=1,
    )

    added = []
    deleted = []

    async def _get(model, pk):
        return {primary.id: primary, duplicate.id: duplicate}.get(pk)

    results = iter(
        [
            _ScalarsResult([dup_email]),  # alias fetch for both identities
            _ScalarsResult([]),           # primary evidence-link ids
            _ScalarsResult([]),           # duplicate evidence links
            _ScalarsResult([]),           # graph edges referencing duplicate
            # Edges already on the primary. Loaded so a re-point that
            # would collide with one of them is deleted instead —
            # merging a genuine duplicate always collides, because one
            # document naming both forms produces an edge to each.
            _ScalarsResult([]),
        ]
    )

    async def _execute(stmt):
        return next(results)

    db = SimpleNamespace(
        get=AsyncMock(side_effect=_get),
        execute=AsyncMock(side_effect=_execute),
        add=lambda obj: added.append(obj),
        delete=AsyncMock(side_effect=lambda obj: deleted.append(obj)),
        flush=AsyncMock(),
        refresh=AsyncMock(),
    )

    with (
        patch(
            "contextedge.services.identity_service.append_operational_event",
            AsyncMock(),
        ),
        patch(
            "contextedge.workers.identity_tasks.rebuild_identity_snapshots"
        ) as rebuild_mock,
    ):
        rebuild_mock.delay = lambda *a, **k: None
        result = await merge_canonical_identities(
            db,
            tenant_id=tenant_id,
            primary_identity_id=primary.id,
            duplicate_identity_id=duplicate.id,
        )

    assert result is primary
    assert primary.resolution_state == "verified"
    assert duplicate.is_active is False
    assert duplicate.metadata_extra["merged_into"] == str(primary.id)
    # Strong alias re-pointed, not deleted.
    assert dup_email.canonical_identity_id == primary.id
    assert deleted == []
    # Duplicate's display name added with a STRING normalized_alias.
    name_aliases = [o for o in added if isinstance(o, IdentityAlias)]
    assert len(name_aliases) == 1
    assert name_aliases[0].alias_text == "J. Smith"
    assert isinstance(name_aliases[0].normalized_alias, str)
    assert name_aliases[0].normalized_alias == "j. smith"


@pytest.mark.asyncio
async def test_merge_strong_alias_text_collision_is_repointed_not_deleted():
    """A duplicate's EMAIL alias whose text equals a primary DISPLAY alias
    must be re-pointed — deleting it would drop the strong identifier."""
    from contextedge.services.identity_service import merge_canonical_identities

    tenant_id = uuid4()
    primary = CanonicalIdentity(
        id=uuid4(), tenant_id=tenant_id, entity_type="person",
        canonical_name="jsmith@acme.com", resolution_state="resolved",
        metadata_extra=None, is_active=True,
    )
    duplicate = CanonicalIdentity(
        id=uuid4(), tenant_id=tenant_id, entity_type="person",
        canonical_name="John Smith", resolution_state="resolved",
        metadata_extra=None, is_active=True,
    )
    dup_email = IdentityAlias(
        id=uuid4(), canonical_identity_id=duplicate.id, tenant_id=tenant_id,
        alias_text="jsmith@acme.com", normalized_alias="jsmith@acme.com",
        alias_type="email", times_observed=1,
    )

    added = []
    deleted = []
    results = iter(
        [
            _ScalarsResult([dup_email]),
            _ScalarsResult([]),
            _ScalarsResult([]),
            _ScalarsResult([]),
            # Edges already on the primary — loaded so a colliding
            # re-point is deleted rather than violating the unique index.
            _ScalarsResult([]),
        ]
    )

    async def _execute(stmt):
        return next(results)

    db = SimpleNamespace(
        get=AsyncMock(side_effect=lambda m, pk: {primary.id: primary, duplicate.id: duplicate}.get(pk)),
        execute=AsyncMock(side_effect=_execute),
        add=lambda obj: added.append(obj),
        delete=AsyncMock(side_effect=lambda obj: deleted.append(obj)),
        flush=AsyncMock(),
        refresh=AsyncMock(),
    )

    with (
        patch(
            "contextedge.services.identity_service.append_operational_event",
            AsyncMock(),
        ),
        patch(
            "contextedge.workers.identity_tasks.rebuild_identity_snapshots"
        ) as rebuild_mock,
    ):
        rebuild_mock.delay = lambda *a, **k: None
        await merge_canonical_identities(
            db,
            tenant_id=tenant_id,
            primary_identity_id=primary.id,
            duplicate_identity_id=duplicate.id,
        )

    # (email, jsmith@acme.com) != (display_name, jsmith@acme.com): re-pointed.
    assert dup_email.canonical_identity_id == primary.id
    assert deleted == []
