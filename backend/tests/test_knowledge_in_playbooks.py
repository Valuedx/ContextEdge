"""Phase 2: approved KB/SOP content reaching playbook generation.

The failure these guard against, stated once:

    SOP:       stop service -> back up certificate -> renew -> restart
    Episodes:  engineer renewed the certificate and restarted
    Generated: renew -> restart          (the backup step is gone)

The generated playbook is not wrong about what happened. It is wrong
about what should happen, and before this nothing in the pipeline could
notice.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from contextedge.ai.generators import playbook_generator
from contextedge.services.knowledge_retrieval_service import (
    MAX_DISTANCE,
    KnowledgeDocument,
    KnowledgeSection,
    build_retrieval_query,
    format_knowledge_block,
    retrieve_knowledge_for_pattern,
)
from contextedge.services.playbook_service import (
    EPISODE_LINK_TYPE,
    EVIDENCE_LINK_TYPE,
    KNOWLEDGE_LINK_TYPE,
    _materialize_evidence_links,
)


def _doc(title="VPN Certificate Renewal SOP", **kw):
    return KnowledgeDocument(
        evidence_id=kw.get("evidence_id", uuid.uuid4()),
        title=title,
        evidence_type=kw.get("evidence_type", "sop"),
        sections=kw.get(
            "sections",
            [
                KnowledgeSection(
                    text="Back up the certificate before renewing.",
                    section_ref="3.1 Backup",
                    page=2,
                )
            ],
        ),
        best_distance=kw.get("best_distance", 0.2),
    )


# --- retrieval query ---------------------------------------------------------


def test_query_uses_episode_facts_not_just_the_pattern_title():
    """An incident titled "Laptop Wi-Fi not working" retrieves nothing
    useful. The same episode's established facts — adapter, error code,
    the action that worked — retrieve the article that documents them.
    That richer fingerprint only exists after reconstruction, which is
    why retrieval runs at pattern time."""
    query = build_retrieval_query(
        pattern_title="Laptop Wi-Fi not working",
        pattern_description=None,
        episode_summaries=[
            {"root_cause": "Intel AX201 driver Code 10", "outcome": "driver rollback"}
        ],
    )
    assert "Intel AX201" in query
    assert "driver rollback" in query


def test_query_is_bounded_and_tolerates_missing_parts():
    assert build_retrieval_query(pattern_title="x", pattern_description=None) == "x"
    assert build_retrieval_query(pattern_title="", pattern_description=None) == ""
    long = build_retrieval_query(
        pattern_title="t",
        pattern_description="d" * 10_000,
        episode_summaries=[{"root_cause": "r" * 10_000}],
    )
    assert len(long) <= 4000


# --- retrieval filtering -----------------------------------------------------


@pytest.mark.asyncio
async def test_only_knowledge_evidence_is_returned():
    """Tickets and chat are not normative. Feeding an incident to the
    generator as "approved knowledge" would give it authority it does
    not have."""
    kb = SimpleNamespace(id=uuid.uuid4(), title="SOP", evidence_type="sop")
    article = SimpleNamespace(id=uuid.uuid4(), title="KB", evidence_type="kb_article")
    ticket = SimpleNamespace(id=uuid.uuid4(), title="INC1", evidence_type="incident")
    chat = SimpleNamespace(id=uuid.uuid4(), title="msg", evidence_type="chat_message")

    with (
        patch(
            "contextedge.search.vector_search.search_evidence_semantic",
            AsyncMock(
                return_value=[(ticket, 0.1), (kb, 0.2), (chat, 0.25), (article, 0.3)]
            ),
        ),
        patch(
            "contextedge.services.knowledge_retrieval_service._attach_sections",
            AsyncMock(),
        ),
    ):
        docs = await retrieve_knowledge_for_pattern(
            SimpleNamespace(), uuid.uuid4(), pattern_title="VPN failure"
        )

    assert [d.evidence_type for d in docs] == ["sop", "kb_article"]


@pytest.mark.asyncio
async def test_weak_semantic_matches_are_dropped():
    """A weak match is worse than none: it hands the generator
    normative-sounding text about the wrong procedure."""
    near = SimpleNamespace(id=uuid.uuid4(), title="near", evidence_type="sop")
    far = SimpleNamespace(id=uuid.uuid4(), title="far", evidence_type="sop")

    with (
        patch(
            "contextedge.search.vector_search.search_evidence_semantic",
            AsyncMock(return_value=[(near, MAX_DISTANCE - 0.01), (far, 0.99)]),
        ),
        patch(
            "contextedge.services.knowledge_retrieval_service._attach_sections",
            AsyncMock(),
        ),
    ):
        docs = await retrieve_knowledge_for_pattern(
            SimpleNamespace(), uuid.uuid4(), pattern_title="VPN"
        )
    assert [d.title for d in docs] == ["near"]


@pytest.mark.asyncio
async def test_retrieval_failure_degrades_to_no_knowledge():
    """Generation without knowledge is what shipped for months. A
    retrieval problem must degrade to that, never block playbook
    creation."""
    with patch(
        "contextedge.search.vector_search.search_evidence_semantic",
        AsyncMock(side_effect=RuntimeError("index down")),
    ):
        docs = await retrieve_knowledge_for_pattern(
            SimpleNamespace(), uuid.uuid4(), pattern_title="VPN"
        )
    assert docs == []


# --- prompt rendering --------------------------------------------------------


def test_knowledge_block_is_labelled_for_citation():
    block = format_knowledge_block([_doc()])
    assert "[kb-1]" in block
    assert "VPN Certificate Renewal SOP" in block
    assert "3.1 Backup" in block
    assert "Back up the certificate" in block


def test_absent_knowledge_is_stated_explicitly():
    """An explicit absence, so the model does not invent normative
    sources to fill a silent gap."""
    block = format_knowledge_block([])
    assert "None found" in block
    assert "observed practice only" in block


def test_image_read_sections_are_marked_as_paraphrase():
    """A section a model read out of a screenshot is not the SOP's exact
    wording, and a step citing it must be reviewable as such."""
    doc = _doc(
        sections=[
            KnowledgeSection(text="admin readwrite", section_ref="4", model_derived=True)
        ]
    )
    assert "read from an image" in format_knowledge_block([doc])


# --- generator wiring --------------------------------------------------------


@pytest.mark.asyncio
async def test_knowledge_reaches_the_prompt_as_a_distinct_input():
    """Not folded into episode summaries: the prompt must treat
    normative and empirical sources differently, and merging them erases
    the distinction a reviewer needs."""
    with patch.object(
        playbook_generator, "llm_complete_json", AsyncMock(return_value={})
    ) as mock:
        await playbook_generator.generate_playbook_candidate(
            "VPN outage",
            "Certificate expiry",
            2,
            [{"title": "EP1", "root_cause": "expired cert"}],
            [],
            knowledge_sources=[_doc()],
        )

    prompt = mock.await_args.args[0]
    assert "APPROVED KNOWLEDGE" in prompt
    assert "[kb-1]" in prompt
    assert "Back up the certificate" in prompt
    # Episodes are labelled so steps can cite them the same way.
    assert "[ep-1]" in prompt


@pytest.mark.asyncio
async def test_generation_works_with_no_knowledge_found():
    with patch.object(
        playbook_generator, "llm_complete_json", AsyncMock(return_value={})
    ) as mock:
        await playbook_generator.generate_playbook_candidate(
            "VPN outage", "", 1, [{"title": "EP1"}], [], knowledge_sources=[]
        )
    assert "None found" in mock.await_args.args[0]


@pytest.mark.asyncio
async def test_older_prompt_versions_still_render():
    """A tenant pinned to v1/v2 by variant routing has no knowledge slot
    in its template; passing one must not raise."""
    from contextedge.ai.prompts import get_prompt

    v2 = get_prompt("playbook", None)
    v2 = SimpleNamespace(
        name="playbook",
        version="v2",
        system="sys",
        user_template="{pattern_title}|{pattern_description}|{episode_count}"
        "|{episode_summaries}|{negative_knowledge}",
        format_user=lambda **kw: "rendered",
    )
    with (
        patch.object(playbook_generator, "get_prompt", return_value=v2),
        patch.object(
            playbook_generator, "llm_complete_json", AsyncMock(return_value={})
        ) as mock,
    ):
        await playbook_generator.generate_playbook_candidate(
            "t", "d", 1, [], [], knowledge_sources=[_doc()]
        )
    assert mock.await_args.args[0] == "rendered"


# --- prompt contract ---------------------------------------------------------


def test_v3_instructs_the_model_to_surface_disagreement_not_resolve_it():
    """The substantive change. Preferring the SOP ignores verified runs;
    preferring practice deletes a safeguard. Both go to the reviewer."""
    from contextedge.ai.prompts import get_prompt

    prompt = get_prompt("playbook", None)
    assert prompt.version == "v3"
    # Whitespace-normalized: the prompt is hard-wrapped, so asserting on
    # raw text would break whenever a line is reflowed — a failure that
    # says nothing about the contract being tested.
    system = " ".join(prompt.system.split())
    assert "do not silently choose" in system
    assert "requires_review" in system
    assert "conflicts" in system
    # And the omission case the whole phase exists for.
    assert "even when no episode performed them" in system
    assert "Never invent a normative source" in system


def test_earlier_prompt_versions_remain_registered_and_immutable():
    from contextedge.ai.prompts import list_prompt_versions

    assert list_prompt_versions("playbook") == ["v1", "v2", "v3"]


# --- provenance --------------------------------------------------------------


def test_knowledge_links_are_written_with_their_own_type():
    """"Which SOP does this playbook implement" must be answerable
    without re-reading the JSON blob — and a knowledge-drift check needs
    to find every version citing an article that changed."""
    added = []
    db = SimpleNamespace(add=added.append)
    version = SimpleNamespace(id=uuid.uuid4())
    ev, ep, kb = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    written = _materialize_evidence_links(
        db,
        version,
        {
            "evidence_ids": [str(ev)],
            "episode_ids": [str(ep)],
            "knowledge_ids": [str(kb)],
        },
    )

    assert written == 3
    by_type = {row.link_type: row for row in added}
    assert by_type[KNOWLEDGE_LINK_TYPE].evidence_id == kb
    assert by_type[EVIDENCE_LINK_TYPE].evidence_id == ev
    assert by_type[EPISODE_LINK_TYPE].episode_id == ep


def test_a_knowledge_id_also_listed_as_evidence_is_not_duplicated():
    """A KB article is an EvidenceItem, so the same id can arrive on both
    lists."""
    added = []
    db = SimpleNamespace(add=added.append)
    shared = uuid.uuid4()
    written = _materialize_evidence_links(
        db,
        SimpleNamespace(id=uuid.uuid4()),
        {"evidence_ids": [str(shared)], "knowledge_ids": [str(shared)]},
    )
    assert written == 1


def test_conflicts_persist_on_the_version():
    """NULL means "not assessed" — versions generated before knowledge
    was an input must not claim the comparison was made and found
    nothing."""
    from contextedge.models.playbook import PlaybookVersion

    assert "conflicts" in PlaybookVersion.__table__.columns
    assert PlaybookVersion.__table__.columns["conflicts"].nullable is True
