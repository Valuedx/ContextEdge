"""Promotion by corroboration.

The failure this prevents is silent and total: on a fresh tenant no identity
is ever `resolved`, so correlation's identity tier trusts nothing, so no
correlation edge is created, so no episode is ever reconstructed. Measured on
the live Zoho corpus at 957 identities and 493 evidence items — zero
episodes, while evidence, tokens and task success all looked healthy.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from contextedge.services.identity_promotion import (
    CORROBORATION_DEGREE_MIN,
    PROMOTABLE_STATE,
    RARE_DEGREE_MAX,
    promote_corroborated_identities,
)


def test_rare_degree_max_matches_the_correlation_tier():
    """If these drift, promotion trusts identities correlation calls common.

    The constant is duplicated only because importing `correlation_service`
    here would close an import cycle. This is the guard that makes the
    duplication safe.
    """
    from contextedge.services.correlation_service import (
        RARE_DEGREE_MAX as CORRELATION_RARE_MAX,
    )

    assert RARE_DEGREE_MAX == CORRELATION_RARE_MAX


def test_a_singleton_cannot_correlate_so_the_floor_is_two():
    # Not a style preference: one evidence item cannot be correlated with
    # itself, so a degree-1 identity contributes nothing if promoted.
    assert CORROBORATION_DEGREE_MIN == 2


def _identity(state=PROMOTABLE_STATE, name="ActiveMQ"):
    return SimpleNamespace(
        id=uuid4(),
        canonical_name=name,
        resolution_state=state,
        resolution_method="unmatched_new",
    )


def _db(returned):
    """A db whose one SELECT yields `returned` (the already-filtered rows)."""
    result = SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: list(returned)))
    return SimpleNamespace(execute=AsyncMock(return_value=result), flush=AsyncMock())


@pytest.mark.asyncio
async def test_corroborated_identity_is_promoted():
    identity = _identity()
    db = _db([identity])

    promoted = await promote_corroborated_identities(db, uuid4(), [identity.id])

    assert promoted == [identity.id]
    assert identity.resolution_state == "resolved"
    assert identity.resolution_method == "corroborated"
    db.flush.assert_awaited()


@pytest.mark.asyncio
async def test_nothing_eligible_writes_nothing():
    """The query does the filtering, so an empty result must not flush."""
    db = _db([])

    promoted = await promote_corroborated_identities(db, uuid4(), [uuid4()])

    assert promoted == []
    db.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_identities_short_circuits():
    db = _db([])
    assert await promote_corroborated_identities(db, uuid4(), []) == []
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_promotion_failure_never_breaks_normalization():
    """Losing a promotion costs a graph edge; raising would cost the evidence."""
    db = SimpleNamespace(
        execute=AsyncMock(side_effect=RuntimeError("connection reset")),
        flush=AsyncMock(),
    )

    assert await promote_corroborated_identities(db, uuid4(), [uuid4()]) == []


@pytest.mark.asyncio
async def test_linking_promotes_through_the_normalization_path():
    """The wiring, not just the function: `link_evidence_identities` must
    call promotion after flushing, or nothing is ever promoted in practice."""
    from unittest.mock import patch

    from contextedge.models.evidence import EvidenceItem
    from contextedge.services import identity_service

    # A real (unsaved) ORM instance: the link row sets a relationship, which
    # SQLAlchemy will not accept against a stand-in object.
    evidence = EvidenceItem(id=uuid4(), canonical_entity_refs=None)
    identity_id = uuid4()
    resolved = [{
        "canonical_id": str(identity_id),
        "canonical_name": "ActiveMQ",
        "entity_type": "service",
        "alias": "activemq",
        "matched_via": "provisional_new",
        "confidence": 0.5,
        "resolution_state": "provisional",
    }]
    db = SimpleNamespace(
        execute=AsyncMock(
            return_value=SimpleNamespace(
                scalars=lambda: SimpleNamespace(all=lambda: [])
            )
        ),
        add=lambda obj: None,
        flush=AsyncMock(),
    )

    with (
        patch.object(
            identity_service, "resolve_entities_from_text",
            new=AsyncMock(return_value=resolved),
        ),
        patch.object(identity_service, "ensure_edge", new=AsyncMock()),
        patch.object(identity_service, "append_operational_event", new=AsyncMock()),
        patch.object(
            identity_service, "promote_corroborated_identities",
            new=AsyncMock(return_value=[identity_id]),
        ) as promote,
    ):
        await identity_service.link_evidence_identities(
            db, tenant_id=uuid4(), evidence=evidence, content="ActiveMQ broker down"
        )

    promote.assert_awaited_once()
    # The linked identity is what gets offered for promotion.
    assert promote.await_args.args[2] == [identity_id]
