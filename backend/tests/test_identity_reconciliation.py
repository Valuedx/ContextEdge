"""Batch reconciliation: finding duplicates per-mention resolution can't.

The layered resolver compares an incoming mention against candidates
that share a substring with it. "SFA" shares none with "Sales Force
Automation", so the two were never presented together and forked into
separate identities — as did "HP UPD" and "HP Universal Print Driver".
On the live tenant, 117 of 204 links resolved as `provisional_new` and
only 7 reached the adjudicator at all.

This pass reads a whole entity type at once, which is the only way those
pairs become visible. It proposes; a human merges.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from contextedge.services.identity_reconciliation_service import (
    MIN_CONFIDENCE,
    _batches,
    _parse_groups,
)


def _identity(name: str, entity_type: str = "application") -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        canonical_name=name,
        entity_type=entity_type,
        metadata_extra=None,
    )


# --- turning the model's answer into pairs ------------------------------------


def test_a_group_becomes_one_proposal_per_duplicate():
    identities = [_identity("Sales Force Automation"), _identity("SFA")]
    proposals = _parse_groups(
        [{"keep_id": 0, "merge_ids": [1], "confidence": 1.0, "reason": "acronym"}],
        identities,
        "application",
    )
    assert len(proposals) == 1
    assert proposals[0].keep_id == identities[0].id
    assert proposals[0].merge_id == identities[1].id


def test_a_group_naming_several_duplicates_fans_out():
    identities = [_identity("Print Spooler"), _identity("Spooler"), _identity("spooler")]
    proposals = _parse_groups(
        [{"keep_id": 0, "merge_ids": [1, 2], "confidence": 0.99, "reason": "same"}],
        identities,
        "service",
    )
    assert {p.merge_id for p in proposals} == {identities[1].id, identities[2].id}


def test_a_hallucinated_index_is_dropped_not_guessed():
    """An out-of-range index would otherwise propose merging two rows the
    model never saw."""
    identities = [_identity("A"), _identity("B")]
    assert (
        _parse_groups(
            [{"keep_id": 0, "merge_ids": [7], "confidence": 1.0}], identities, "service"
        )
        == []
    )
    assert (
        _parse_groups(
            [{"keep_id": 9, "merge_ids": [1], "confidence": 1.0}], identities, "service"
        )
        == []
    )


def test_a_group_merging_a_record_into_itself_is_dropped():
    identities = [_identity("A"), _identity("B")]
    assert (
        _parse_groups(
            [{"keep_id": 0, "merge_ids": [0], "confidence": 1.0}], identities, "service"
        )
        == []
    )


def test_low_confidence_never_reaches_a_reviewer():
    """The model is told abstaining is free, so a lower score means it
    guessed rather than declined.

    Measured on the live graph, this separates cleanly: everything scored
    1.00 was correct, and the 0.90 band was roles, placeholders, and one
    outright error — folding a general "Spooler service" into "Spooler
    service on PRINTSRV04", which narrows a service to a single host.
    """
    identities = [_identity("A"), _identity("B")]
    group = {"keep_id": 0, "merge_ids": [1], "confidence": MIN_CONFIDENCE - 0.01}
    assert _parse_groups([group], identities, "service") == []

    group["confidence"] = MIN_CONFIDENCE
    assert len(_parse_groups([group], identities, "service")) == 1


def test_malformed_groups_do_not_break_the_run():
    identities = [_identity("A"), _identity("B")]
    junk = ["not a dict", {"keep_id": 0}, {"keep_id": 0, "merge_ids": "1"}, {}]
    assert _parse_groups(junk, identities, "service") == []


# --- batching -----------------------------------------------------------------


def test_a_small_set_is_one_batch():
    assert _batches(list(range(10)), 60, 10) == [list(range(10))]


def test_batches_overlap_so_a_pair_cannot_straddle_a_boundary():
    """The list is ordered by name, so near-duplicates cluster exactly at
    boundaries. A clean split would hide the very pairs this pass
    exists to find."""
    items = list(range(100))
    batches = _batches(items, 60, 10)
    assert len(batches) > 1
    covered = {value for batch in batches for value in batch}
    assert covered == set(items)
    # Consecutive batches share rows.
    assert set(batches[0]) & set(batches[1])


def test_every_item_appears_in_some_batch():
    for size in (7, 13, 60):
        items = list(range(101))
        covered = {v for batch in _batches(items, size, 3) for v in batch}
        assert covered == set(items)


# --- what the merge does ------------------------------------------------------


def test_merging_repoints_graph_edges_without_colliding():
    """The bug that made merging a genuine duplicate fail.

    A document reading "Sales Force Automation (SFA)" produces a
    mentions_identity edge to each identity, so folding one into the
    other is exactly the case that collides with
    uq_graph_edges_active_logical. The evidence-link loop guarded for
    this; the edge loop blindly UPDATEd — so the closer two identities
    were to being the same thing, the more certainly the merge 500'd.
    """
    import inspect

    from contextedge.services import identity_service

    source = inspect.getsource(identity_service.merge_canonical_identities)
    guard = source.index("primary_edge_keys")
    repoint = source.index("edge.source_node_id = source_id")
    assert guard < repoint
    assert "db.delete(edge)" in source


@pytest.mark.asyncio
async def test_accepting_a_proposal_merges_and_rejecting_does_not():
    from unittest.mock import AsyncMock, patch

    from contextedge.services import identity_reconciliation_service as svc

    tenant = uuid.uuid4()
    proposal = SimpleNamespace(
        tenant_id=tenant,
        status="pending",
        primary_identity_id=uuid.uuid4(),
        duplicate_identity_id=uuid.uuid4(),
        decided_by=None,
        decided_at=None,
    )
    db = SimpleNamespace(get=AsyncMock(return_value=proposal), flush=AsyncMock())

    with patch.object(
        svc, "reconcile_identities", AsyncMock()
    ), patch(
        "contextedge.services.identity_service.merge_canonical_identities",
        AsyncMock(),
    ) as merge:
        await svc.decide_proposal(db, tenant, uuid.uuid4(), accept=False, actor_id=None)
        assert proposal.status == "rejected"
        merge.assert_not_awaited()

        proposal.status = "pending"
        await svc.decide_proposal(db, tenant, uuid.uuid4(), accept=True, actor_id=None)
        assert proposal.status == "accepted"
        merge.assert_awaited_once()


@pytest.mark.asyncio
async def test_an_already_decided_proposal_is_not_decided_twice():
    """Accepting twice would attempt a second merge against an identity
    already folded away."""
    from unittest.mock import AsyncMock

    from contextedge.services import identity_reconciliation_service as svc

    tenant = uuid.uuid4()
    proposal = SimpleNamespace(tenant_id=tenant, status="rejected")
    db = SimpleNamespace(get=AsyncMock(return_value=proposal), flush=AsyncMock())

    result = await svc.decide_proposal(
        db, tenant, uuid.uuid4(), accept=True, actor_id=None
    )
    assert result.status == "rejected"


@pytest.mark.asyncio
async def test_a_proposal_from_another_tenant_is_invisible():
    from unittest.mock import AsyncMock

    from contextedge.services import identity_reconciliation_service as svc

    proposal = SimpleNamespace(tenant_id=uuid.uuid4(), status="pending")
    db = SimpleNamespace(get=AsyncMock(return_value=proposal), flush=AsyncMock())
    assert (
        await svc.decide_proposal(
            db, uuid.uuid4(), uuid.uuid4(), accept=True, actor_id=None
        )
        is None
    )


# --- candidate generation -----------------------------------------------------
#
# The adjudicator only ever judges what this query returns, so the query
# is the real ceiling on resolution quality. Replaying every existing
# identity name as an incoming mention: the substring query returned
# candidates for 33% of them, the trigram query for 52%. "agents"
# returned NOTHING and so forked from "agent" in silence.


def test_candidates_are_ordered_by_closeness_not_alphabetically():
    """The second half of the problem.

    With more matches than the limit, the five kept were whichever sorted
    first — so the right answer could be in the table, match the filter,
    and still never reach the model.
    """
    import inspect

    from contextedge.services import identity_service

    source = inspect.getsource(identity_service._candidate_identities)
    assert "similarity.desc()" in source
    ordering = source.index("similarity.desc()")
    fallback = source.index("identity.trigram_unavailable")
    # The similarity ordering belongs to the primary query, not the
    # fallback that runs when pg_trgm is missing.
    assert ordering < fallback


def test_substring_matching_is_kept_alongside_similarity():
    """Trigram is an addition, not a replacement: a short token inside a
    long name scores low on similarity but is still a real match."""
    import inspect

    from contextedge.services import identity_service

    source = inspect.getsource(identity_service._candidate_identities)
    assert "substring_match" in source
    assert "or_(" in source


def test_a_missing_extension_degrades_instead_of_failing():
    """pg_trgm needs privileges a deployment may withhold. Fewer
    candidates is degraded; no candidates is broken."""
    import inspect

    from contextedge.services import identity_service

    source = inspect.getsource(identity_service._candidate_identities)
    assert "ProgrammingError" in source
    # A SAVEPOINT, not a bare rollback: Postgres aborts the whole
    # transaction on a failed statement, and resolution runs inside
    # evidence normalization with plenty already written.
    assert "begin_nested" in source
    assert "await db.rollback()" not in source


def test_the_adjudicator_is_told_numbered_siblings_are_distinct():
    """Trigram surfaces MAILGW01 next to MAILGW02 — textually near,
    genuinely different machines. Raising recall into a judge without
    telling it what the new near-misses look like trades a silent fork
    for a silent wrong link."""
    from contextedge.ai.prompts import get_prompt

    prompt = get_prompt("identity_adjudication")
    assert prompt.version == "v2"
    system = prompt.system.lower()
    assert "number" in system
    assert "new_identity" in system


# --- cross-type duplicates ----------------------------------------------------
#
# "JMX" existed in the live graph as both a service and an application.
# Every match layer compared entity_type with `==`, and reconciliation
# looped one type at a time, so nothing in the system could ever bring
# those two rows together — the same blindness as per-mention
# adjudication, one level up.


def test_application_and_service_are_treated_as_one_kind():
    """The extractor labels a named software component either way
    depending on a document's wording, so the same thing forks on the
    label rather than on the name."""
    from contextedge.services.identity_service import compatible_entity_types

    assert compatible_entity_types("application") == ["application", "service"]
    assert compatible_entity_types("service") == ["application", "service"]


@pytest.mark.parametrize("entity_type", ["person", "device", "version", "vendor"])
def test_every_other_type_stays_alone(entity_type):
    """The scoping exists so "Phoenix" the application never matches
    "Phoenix" the person. Widening past the one confusable pair would
    give back exactly that protection."""
    from contextedge.services.identity_service import compatible_entity_types

    assert compatible_entity_types(entity_type) == [entity_type]


def test_confusable_types_are_reconciled_in_one_pass():
    from contextedge.services.identity_reconciliation_service import _type_groups

    assert _type_groups(["application", "device", "person", "service"]) == [
        ["application", "service"],
        ["device"],
        ["person"],
    ]


def test_a_type_with_no_sibling_present_still_gets_a_pass():
    """A tenant with applications but no services must not end up with an
    empty group and no reconciliation."""
    from contextedge.services.identity_reconciliation_service import _type_groups

    assert _type_groups(["application"]) == [["application"]]
    assert _type_groups(["service", "device"]) == [["service"], ["device"]]


def test_all_match_layers_use_the_group_not_exact_equality():
    """Alias matching and candidate generation both have to widen, or
    resolution keeps creating the forks reconciliation then cleans up."""
    import inspect

    from contextedge.services import identity_service

    for fn in (
        identity_service._find_exact_alias_match,
        identity_service._candidate_identities,
    ):
        source = inspect.getsource(fn)
        assert "compatible_entity_types" in source
        assert "entity_type == entity.entity_type" not in source


def test_a_cross_type_merge_says_so_in_its_reason():
    """It changes what KIND of thing the surviving record is. A reviewer
    approving "fold X into Y" deserves to know that is part of it."""
    from contextedge.services.identity_reconciliation_service import _parse_groups

    keep = SimpleNamespace(
        id=uuid.uuid4(), canonical_name="JMX", entity_type="application",
        metadata_extra=None,
    )
    duplicate = SimpleNamespace(
        id=uuid.uuid4(), canonical_name="JMX", entity_type="service",
        metadata_extra=None,
    )
    proposals = _parse_groups(
        [{"keep_id": 0, "merge_ids": [1], "confidence": 1.0, "reason": "same tool"}],
        [keep, duplicate],
    )
    assert len(proposals) == 1
    assert "recorded as service" in proposals[0].reason
    assert "would become application" in proposals[0].reason
    # The keeper's type survives the merge, so that is the proposal's type.
    assert proposals[0].entity_type == "application"


def test_a_same_type_merge_is_not_annotated():
    from contextedge.services.identity_reconciliation_service import _parse_groups

    a = SimpleNamespace(
        id=uuid.uuid4(), canonical_name="Agents", entity_type="application",
        metadata_extra=None,
    )
    b = SimpleNamespace(
        id=uuid.uuid4(), canonical_name="agent", entity_type="application",
        metadata_extra=None,
    )
    proposals = _parse_groups(
        [{"keep_id": 0, "merge_ids": [1], "confidence": 1.0, "reason": "plural"}],
        [a, b],
    )
    assert proposals[0].reason == "plural"


def test_the_type_column_is_only_rendered_for_mixed_batches():
    """A constant repeated on every line reads as a signal."""
    from contextedge.services.identity_reconciliation_service import _render

    identity = SimpleNamespace(
        canonical_name="JMX", entity_type="service", metadata_extra=None
    )
    assert "recorded as" not in _render(identity, [], 0, show_type=False)
    assert "recorded as: service" in _render(identity, [], 0, show_type=True)
