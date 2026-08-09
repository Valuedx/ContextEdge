"""A4: claim population machinery — shipped dormant behind v2.

The v3 A/B failed label stability (4/8, recorded in the prompt
registry), so v2 stays default and the claims list is empty in
production. These tests pin the machinery for when a claims-bearing
prompt passes: parsing rejects junk, persistence dedupes tenant-wide,
new claims are invisible to the projection until validated.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from contextedge.ai.classifiers.relevance import _parse_claims
from contextedge.services import claim_service


def test_parse_rejects_junk_and_caps_at_three():
    raw = [
        {"type": "symptom", "text": "Agent fails to start", "confidence": 0.9},
        {"type": "invented_type", "text": "nope"},
        {"type": "failed_step", "text": "   "},
        "not a dict",
        {"type": "user_impact", "text": "Workflows blocked", "confidence": "junk"},
        {"type": "symptom", "text": "x" * 500, "confidence": 2.5},
        {"type": "symptom", "text": "past the cap"},
    ]
    parsed = _parse_claims(raw[:3]) + _parse_claims(raw[3:])
    out = _parse_claims(raw)
    assert len(out) <= 3
    assert all(c["type"] in {"symptom", "failed_step", "user_impact",
                             "probable_root_cause", "recommended_action"} for c in out)
    assert all(0.0 <= c["confidence"] <= 1.0 for c in out)
    assert all(len(c["text"]) <= 300 for c in out)
    del parsed


def test_prompt_default_stays_v2_after_failed_ab():
    """v3 moved 4/8 borderline labels (measured 2026-08-07) — an added
    output requirement is a behavior change, not a free field."""
    from contextedge.ai import prompts as prompts_mod

    assert prompts_mod._DEFAULTS["relevance"] == "v2"
    assert "v3" in prompts_mod._REGISTRY["relevance"]


@pytest.mark.asyncio
async def test_new_claim_lands_unverified_with_supported_by_edge():
    """claim->evidence uses the SUPPORT vocabulary (matching the
    materializer); asserted_in is reserved for claim->session."""
    db = MagicMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    lookup = MagicMock()
    lookup.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(return_value=lookup)
    evidence = SimpleNamespace(id=uuid.uuid4(), domain_id=None)
    with patch("contextedge.graph.builder.ensure_edge", new=AsyncMock()) as edge:
        counts = await claim_service.persist_extracted_claims(
            db, uuid.uuid4(), evidence,
            [{"type": "symptom", "text": "Agent fails to start", "confidence": 0.9}],
        )
    assert counts == {"created": 1, "linked": 1}
    claim = db.add.call_args.args[0]
    assert claim.validation_status == "unverified"
    assert claim.created_by_type == "agent"
    assert edge.await_args.kwargs["edge_type"] == "supported_by"
    assert edge.await_args.kwargs["target_type"] == "evidence"


@pytest.mark.asyncio
async def test_reasserted_claim_links_instead_of_duplicating():
    existing = SimpleNamespace(id=uuid.uuid4())
    db = MagicMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    lookup = MagicMock()
    lookup.scalar_one_or_none.return_value = existing
    db.execute = AsyncMock(return_value=lookup)
    evidence = SimpleNamespace(id=uuid.uuid4(), domain_id=None)
    with patch("contextedge.graph.builder.ensure_edge", new=AsyncMock()) as edge:
        counts = await claim_service.persist_extracted_claims(
            db, uuid.uuid4(), evidence,
            [{"type": "symptom", "text": "Agent Fails To Start", "confidence": 0.8}],
        )
    assert counts == {"created": 0, "linked": 1}
    db.add.assert_not_called()
    assert edge.await_args.kwargs["source_id"] == existing.id
