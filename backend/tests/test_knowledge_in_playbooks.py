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
    _section_purpose,
    _select_prompt_sections,
    build_retrieval_query,
    format_knowledge_block,
    knowledge_refs_payload,
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
    useful. The same episode's established facts â€” adapter, error code,
    the action that worked â€” retrieve the article that documents them.
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


def test_retrieval_query_includes_mail_thread_solution_under_episode():
    query = build_retrieval_query(
        pattern_title="Agent stopped",
        pattern_description=None,
        episode_summaries=[
            {
                "title": "Agent unknown state",
                "root_cause": "stale PID",
                "outcome": "restarted",
                "steps": [
                    {"type": "remediation", "text": "Restarted AE agent service"}
                ],
                "thread_solutions": ["Resolved by deleting the PID file"],
            }
        ],
    )
    assert "Restarted AE agent service" in query
    assert "PID file" in query


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
                # All four inside MAX_DISTANCE (0.25, derived from the link
                # threshold) â€” this test is about the TYPE filter, and its
                # fixtures must not straddle the distance ceiling or it
                # silently becomes a threshold test.
                return_value=[(ticket, 0.1), (kb, 0.15), (chat, 0.2), (article, 0.22)]
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


@pytest.mark.asyncio
async def test_embedding_fetch_keeps_older_kb_and_labels_version_gap():
    """Fetch is embedding-nearest, not a hardcoded version query.

    A 7* ticket still receives the 8.x article when it is a close
    semantic neighbour — that KB may be the full procedure still in use
    — but matching stored version ranks first, and the mismatch is
    labelled so the playbook can name it on the step.
    """
    keep = SimpleNamespace(
        id=uuid.uuid4(),
        title="Fix on 7.x",
        evidence_type="kb_article",
        knowledge_state="published",
        applicability={"product_versions": {"_platform": "7*"}, "components": []},
        source_facets={"version": "7*"},
        body_text="Affected Version: 7.x",
    )
    other_major = SimpleNamespace(
        id=uuid.uuid4(),
        title="Fix on 8.x",
        evidence_type="kb_article",
        knowledge_state="published",
        applicability={"product_versions": {"ae": "8.2.3"}, "components": []},
        source_facets={"version": "8.2.3"},
        body_text="Affected Version: 8.2.3",
    )
    unversioned = SimpleNamespace(
        id=uuid.uuid4(),
        title="Generic plugin timeout",
        evidence_type="kb_article",
        knowledge_state="published",
        applicability={"product_versions": {}, "components": []},
        source_facets={},
        body_text="Restart the plugin.",
    )

    with (
        patch(
            "contextedge.search.vector_search.search_evidence_semantic",
            AsyncMock(
                return_value=[(other_major, 0.1), (keep, 0.12), (unversioned, 0.14)]
            ),
        ),
        patch(
            "contextedge.services.knowledge_retrieval_service._attach_sections",
            AsyncMock(),
        ),
    ):
        docs = await retrieve_knowledge_for_pattern(
            SimpleNamespace(),
            uuid.uuid4(),
            pattern_title="Plugin timeout",
            custom_fields={"version": "7*"},
        )

    assert [d.title for d in docs] == [
        "Fix on 7.x",
        "Fix on 8.x",
        "Generic plugin timeout",
    ]
    matched, mismatched, agnostic = docs
    assert matched.product_version == "7*"
    assert matched.ticket_version == "7*"
    assert matched.version_conflict is None
    assert mismatched.version_conflict == ("8.2.3", "7*")
    assert agnostic.product_version is None
    block = format_knowledge_block([mismatched])
    assert "PRODUCT VERSION MISMATCH" in block
    assert "8.2.3" in block
    assert "7*" in block
    assert "based on the 8.2.3 KB" in block


@pytest.mark.asyncio
async def test_stored_source_facets_version_is_used_without_reparse():
    """Ingest already wrote source_facets.version. Retrieval must use
    that stored facet rather than a hardcoded version fetch."""
    article = SimpleNamespace(
        id=uuid.uuid4(),
        title="Agent unknown state",
        evidence_type="kb_article",
        knowledge_state="published",
        applicability={},
        source_facets={"version": "8*"},
        body_text="Restart the agent.",
    )
    with (
        patch(
            "contextedge.search.vector_search.search_evidence_semantic",
            AsyncMock(return_value=[(article, 0.1)]),
        ),
        patch(
            "contextedge.services.knowledge_retrieval_service._attach_sections",
            AsyncMock(),
        ),
    ):
        docs = await retrieve_knowledge_for_pattern(
            SimpleNamespace(),
            uuid.uuid4(),
            pattern_title="Agent unknown",
            custom_fields={"version": "8.2.3"},
        )
    assert len(docs) == 1
    assert docs[0].product_version == "8*"
    assert docs[0].ticket_version == "8.2.3"
    assert docs[0].version_conflict is None


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


def test_knowledge_block_labels_procedural_section_purpose():
    doc = _doc(
        sections=[
            KnowledgeSection(
                text="Restart the AutomationEdge Agent service.",
                section_ref="Resolution",
                purpose="action",
            ),
            KnowledgeSection(
                text="Verify the request leaves NEW state.",
                section_ref="Validation",
                purpose="validation",
            ),
        ]
    )
    block = format_knowledge_block([doc])
    assert "structure-derived" in block
    assert "QUALITY CONTRACT" in block
    assert "ACTION" in block
    assert "VALIDATION" in block


def test_action_sections_are_promoted_from_oversampled_chunks():
    """A semantically close overview should not crowd the procedure out of
    the prompt. Product-specific action/check sections are what make the
    generated playbook exact instead of generic."""
    chunks = [
        SimpleNamespace(chunk_index=0, parent_section="Overview", text="Agent issue"),
        SimpleNamespace(chunk_index=1, parent_section="Background", text="About agents"),
        SimpleNamespace(chunk_index=2, parent_section="Symptoms", text="Requests stay new"),
        SimpleNamespace(chunk_index=3, parent_section="Notes", text="General information"),
        SimpleNamespace(chunk_index=4, parent_section="History", text="Old details"),
        SimpleNamespace(chunk_index=5, parent_section="Scope", text="AE server"),
        SimpleNamespace(
            chunk_index=6,
            parent_section="Resolution",
            text="Restart the AutomationEdge Agent service.",
        ),
    ]

    selected = _select_prompt_sections(chunks)

    assert chunks[0] in selected
    assert chunks[-1] in selected
    assert _section_purpose(chunks[-1]) == "action"


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
async def test_mail_thread_solution_sits_under_the_episode_with_kb():
    """Playbooks must use both sources: KB as what should be done, and
    the mail-thread solution under the episode as what actually worked."""
    with patch.object(
        playbook_generator, "llm_complete_json", AsyncMock(return_value={})
    ) as mock:
        await playbook_generator.generate_playbook_candidate(
            "Agent stopped",
            "Unknown state",
            1,
            [
                {
                    "id": str(uuid.uuid4()),
                    "title": "Agent unknown state",
                    "root_cause": "stale PID file",
                    "outcome": "agent running",
                    "steps": [
                        {
                            "type": "remediation",
                            "text": "Restarted the AutomationEdge Agent service",
                        }
                    ],
                    "thread_solutions": [
                        "Resolved by restarting the agent from services.msc; queue drained."
                    ],
                }
            ],
            [],
            knowledge_sources=[_doc()],
        )
    prompt = mock.await_args.args[0]
    assert "[kb-1]" in prompt
    assert "[ep-1]" in prompt
    assert "Observed steps (from mail thread)" in prompt
    assert "Mail-thread solution:" in prompt
    assert "Restarted the AutomationEdge Agent service" in prompt
    assert "services.msc" in prompt
    # Still distinct inputs — not folded into one blob.
    assert "APPROVED KNOWLEDGE" in prompt
    assert "EPISODES" in prompt


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


def test_default_prompt_instructs_the_model_to_surface_disagreement_not_resolve_it():
    """The substantive v3 change, asserted on the DEFAULT version so the
    contract survives version bumps. Preferring the SOP ignores verified
    runs; preferring practice deletes a safeguard. Both go to the reviewer."""
    from contextedge.ai.prompts import get_prompt

    prompt = get_prompt("playbook", None)
    assert prompt.version == "v9"  # mail-thread solutions under episodes
    # Whitespace-normalized: the prompt is hard-wrapped, so asserting on
    # raw text would break whenever a line is reflowed â€” a failure that
    # says nothing about the contract being tested.
    system = " ".join(prompt.system.split())
    assert "do not silently choose" in system
    assert "requires_review" in system
    assert "conflicts" in system
    # And the omission case the whole phase exists for.
    assert "even when no episode performed them" in system
    assert "Never invent a normative source" in system
    # v4 contract: commands verbatim, prompt labels out of prose, unsourced
    # steps state their verification.
    assert "reproduce it EXACTLY" in system
    assert "NOWHERE in prose" in system
    assert "what observable result would confirm" in system
    # v7 contract: labelled KB action/check/rollback sections are a coverage
    # checklist for the final generated playbook.
    assert "Treat labelled KB sections as a coverage checklist" in system
    assert "product-specific action" in system
    assert "If any required item is missing" in system
    # v8 contract: a different-release KB is still used, and the step
    # text itself names the KB product version vs the ticket version.
    assert "PRODUCT VERSION MISMATCH" in system
    assert "Based on KB for AutomationEdge" in system
    # v9 contract: mail-thread solutions sit under each episode and are
    # used together with KB, not instead of it.
    assert "Use BOTH sources" in system
    assert "Mail-thread solution" in system


def test_earlier_prompt_versions_remain_registered_and_immutable():
    from contextedge.ai.prompts import list_prompt_versions

    # v8 added: name KB vs ticket product version on the step itself.
    # Earlier versions stay for eval baselines and historical llm.usage
    # attribution.
    assert list_prompt_versions("playbook") == [
        "v1",
        "v2",
        "v3",
        "v4",
        "v5",
        "v6",
        "v7",
        "v8",
        "v9",
    ]


# --- provenance --------------------------------------------------------------


def test_knowledge_links_are_written_with_their_own_type():
    """"Which SOP does this playbook implement" must be answerable
    without re-reading the JSON blob â€” and a knowledge-drift check needs
    to find every version citing an article that changed."""
    added = []
    db = SimpleNamespace(add=added.append)
    version = SimpleNamespace(id=uuid.uuid4(), tenant_id=uuid.uuid4())
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
        SimpleNamespace(id=uuid.uuid4(), tenant_id=uuid.uuid4()),
        {"evidence_ids": [str(shared)], "knowledge_ids": [str(shared)]},
    )
    assert written == 1


def test_conflicts_persist_on_the_version():
    """NULL means "not assessed" â€” versions generated before knowledge
    was an input must not claim the comparison was made and found
    nothing."""
    from contextedge.models.playbook import PlaybookVersion

    assert "conflicts" in PlaybookVersion.__table__.columns
    assert PlaybookVersion.__table__.columns["conflicts"].nullable is True


def test_knowledge_refs_payload_records_ids_and_ticket_version():
    """The version blob must record the same articles the generator saw,
    plus the ticket AE version used to filter them — otherwise a
    reviewer cannot tell which release the playbook was matched against."""
    doc = _doc()
    payload = knowledge_refs_payload([doc], ticket_version="7*")
    assert payload["knowledge_ids"] == [str(doc.evidence_id)]
    assert payload["ticket_version"] == "7*"
    assert payload["knowledge"][0]["title"] == doc.title
    assert payload["knowledge"][0]["product_version"] is None
    assert payload["knowledge"][0]["version_mismatch"] is None
    assert "ticket_version" not in knowledge_refs_payload([doc])


def test_knowledge_refs_payload_records_version_mismatch():
    doc = _doc()
    doc.product_version = "7*"
    doc.ticket_version = "8.2.3"
    doc.version_conflict = ("7*", "8.2.3")
    payload = knowledge_refs_payload([doc], ticket_version="8.2.3")
    assert payload["knowledge"][0]["product_version"] == "7*"
    assert payload["knowledge"][0]["version_mismatch"] == ["7*", "8.2.3"]


def test_api_generate_persists_retrieved_knowledge_not_model_evidence_refs():
    """POST /playbooks/generate used to forward the LLM candidate whole.
    The model cites [kb-N] in steps but does not emit knowledge_ids, so
    those playbooks used KB in the prompt with no stored provenance."""
    import inspect

    from contextedge.api.v1 import playbooks

    source = inspect.getsource(playbooks.generate_playbook)
    assert "knowledge_refs_payload" in source
    assert "evidence_ids_for_episodes" in source
    assert "ticket_version_custom_fields" in source
    assert 'candidate["evidence_refs"]' in source
    assert "playbook_episode_summaries" in source


def test_playbook_eval_passes_ticket_version_into_retrieval():
    """A/B evals that skip the ticket version would score playbooks
    generated with the wrong-major KB still in the prompt."""
    import inspect

    from contextedge.evals.playbook_model_ab import build_inputs

    source = inspect.getsource(build_inputs)
    assert "ticket_version_custom_fields" in source
    assert "custom_fields=version_fields" in source
    assert "playbook_episode_summaries" in source


def test_thread_solutions_prefer_resolution_language_from_the_mail_tail():
    from contextedge.services.episode_service import thread_solutions_from_messages

    quotes = "On Mon, customer wrote: please look into this. " * 30
    fix = "Resolved by restarting the agent from services.msc"
    out = thread_solutions_from_messages(
        [
            ("please look into this", None),
            (quotes + fix, None),
        ]
    )
    assert len(out) == 1
    assert "services.msc" in out[0]


def test_playbook_steps_keep_mail_thread_remediation():
    from contextedge.services.episode_service import playbook_steps_from_rows

    rows = [
        SimpleNamespace(
            step_order=1, step_type="complaint", text="Agent is down", observation=None
        ),
        SimpleNamespace(
            step_order=2,
            step_type="remediation",
            text="Restarted the AutomationEdge Agent service",
            observation="Agent running",
        ),
    ]
    steps = playbook_steps_from_rows(rows)
    assert any(s["type"] == "remediation" and "Agent service" in s["text"] for s in steps)
