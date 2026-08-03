"""Step-level citation validation in playbook generation.

Phase 2 generated and persisted `source_refs` and nothing checked them.
An unverified citation is worse than none: it survives review precisely
because it looks like provenance.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from contextedge.ai.generators import playbook_generator
from contextedge.ai.generators.playbook_generator import (
    _build_ref_map,
    validate_source_refs,
)


def _map():
    return _build_ref_map(
        [SimpleNamespace(evidence_id=uuid.uuid4(), title="VPN SOP")],
        [{"id": str(uuid.uuid4()), "title": "EP1"}],
    )


def test_supplied_labels_resolve_to_durable_references():
    """"kb-1" means nothing once the prompt that defined it is gone. A
    persisted step citing a bare label is unreviewable a week later."""
    ref_map = _map()
    result = {"steps": [{"order": 1, "source_refs": ["kb-1", "ep-1"]}]}
    validate_source_refs(result, ref_map)

    refs = result["steps"][0]["source_refs"]
    assert [r["label"] for r in refs] == ["kb-1", "ep-1"]
    assert [r["kind"] for r in refs] == ["knowledge", "episode"]
    for ref in refs:
        uuid.UUID(ref["id"])  # resolves to a real id, not a label


def test_kind_is_preserved_because_normative_and_empirical_differ():
    """The whole premise of phase 2 is that these are different claims.
    A resolved citation that lost which one it was is half a citation."""
    ref_map = _map()
    result = {"steps": [{"source_refs": ["kb-1"]}, {"source_refs": ["ep-1"]}]}
    validate_source_refs(result, ref_map)
    assert result["steps"][0]["source_refs"][0]["kind"] == "knowledge"
    assert result["steps"][1]["source_refs"][0]["kind"] == "episode"


def test_minted_citations_are_dropped_and_counted():
    """A model that invents kb-7 produces a step carrying an
    authoritative-looking reference to a document that does not exist.
    Counted, not silently removed, so a prompt that starts hallucinating
    is visible in the counters."""
    ref_map = _map()
    result = {
        "steps": [
            {"order": 1, "source_refs": ["kb-1", "kb-7"]},
            {"order": 2, "source_refs": ["ep-99", "nonsense"]},
        ]
    }
    counts = validate_source_refs(result, ref_map)

    assert counts == {"kept": 1, "dropped": 3}
    assert len(result["steps"][0]["source_refs"]) == 1
    assert result["steps"][1]["source_refs"] == []
    assert result["citation_validation"] == counts


def test_bracketed_labels_are_accepted():
    ref_map = _map()
    result = {"steps": [{"source_refs": ["[kb-1]"]}]}
    validate_source_refs(result, ref_map)
    assert result["steps"][0]["source_refs"][0]["label"] == "kb-1"


def test_duplicate_citations_collapse():
    ref_map = _map()
    result = {"steps": [{"source_refs": ["kb-1", "kb-1", "[kb-1]"]}]}
    validate_source_refs(result, ref_map)
    assert len(result["steps"][0]["source_refs"]) == 1


def test_conflicts_are_validated_too():
    """A conflict block cites its sources the same way a step does, and
    an invented citation there misleads exactly the reviewer the
    conflict was surfaced for."""
    ref_map = _map()
    result = {"conflicts": [{"topic": "restart", "source_refs": ["kb-1", "kb-9"]}]}
    counts = validate_source_refs(result, ref_map)
    assert counts["dropped"] == 1
    assert len(result["conflicts"][0]["source_refs"]) == 1


@pytest.mark.parametrize("raw", [None, "kb-1", 42, {}])
def test_malformed_source_refs_become_empty(raw):
    ref_map = _map()
    result = {"steps": [{"source_refs": raw}]}
    validate_source_refs(result, ref_map)
    assert result["steps"][0]["source_refs"] == []


def test_steps_without_citations_are_marked_explicitly():
    """An explicit empty list, so "the model cited nothing" is
    distinguishable from "the field was never populated"."""
    ref_map = _map()
    result = {"steps": [{"order": 1, "text": "inferred step"}]}
    validate_source_refs(result, ref_map)
    assert result["steps"][0]["source_refs"] == []


def test_no_knowledge_supplied_means_no_knowledge_citation_survives():
    """The prompt forbids inventing a normative source. This enforces
    it: with nothing supplied, every kb-N is minted by definition."""
    ref_map = _build_ref_map([], [{"id": str(uuid.uuid4()), "title": "EP1"}])
    result = {"steps": [{"source_refs": ["kb-1", "ep-1"]}]}
    counts = validate_source_refs(result, ref_map)
    assert counts == {"kept": 1, "dropped": 1}
    assert result["steps"][0]["source_refs"][0]["kind"] == "episode"


@pytest.mark.asyncio
async def test_generation_validates_before_returning():
    """Wired, not just available — the defect was that nothing called it."""
    # The real dataclass: the generator renders it into the prompt, so a
    # stand-in without to_prompt_block would test a path production
    # never takes.
    from contextedge.services.knowledge_retrieval_service import KnowledgeDocument

    doc = KnowledgeDocument(
        evidence_id=uuid.uuid4(), title="SOP", evidence_type="sop", sections=[]
    )
    raw = {"steps": [{"order": 1, "source_refs": ["kb-1", "kb-88"]}]}

    with patch.object(
        playbook_generator, "llm_complete_json", AsyncMock(return_value=raw)
    ):
        out = await playbook_generator.generate_playbook_candidate(
            "VPN outage",
            "",
            1,
            [{"id": str(uuid.uuid4()), "title": "EP1"}],
            [],
            knowledge_sources=[doc],
        )

    assert out["citation_validation"] == {"kept": 1, "dropped": 1}
    assert len(out["steps"][0]["source_refs"]) == 1


@pytest.mark.asyncio
async def test_non_dict_model_output_does_not_crash_generation():
    with patch.object(
        playbook_generator, "llm_complete_json", AsyncMock(return_value=["junk"])
    ):
        out = await playbook_generator.generate_playbook_candidate(
            "t", "", 0, [], [], knowledge_sources=[]
        )
    assert out == ["junk"]
