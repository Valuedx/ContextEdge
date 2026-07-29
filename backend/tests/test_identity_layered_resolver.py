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
