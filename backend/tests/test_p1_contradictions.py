"""Tests for the redesigned contradiction_service.

Covers the embedding pre-filter + incremental cursor + budget cap
redesign shipped as Weeks 3-4 item 1 of the enterprise-architecture
roadmap.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import pytest

from contextedge.models.events import OperationalEvent
from contextedge.models.pattern import (
    Contradiction,
    ContradictionScanState,
    GraphEdge,
)
from contextedge.services.contradiction_service import (
    DEFAULT_SCAN_BUDGET,
    DEFAULT_TOP_K_CANDIDATES,
    _needs_rescan,
    scan_contradictions,
    should_compare_contradiction,
)


# ---------------------------------------------------------------------------
# Helpers — execute-result stubs matching the new call sequence
# ---------------------------------------------------------------------------


class _ScalarsResult:
    def __init__(self, values):
        self._values = values

    def scalars(self):
        return SimpleNamespace(all=lambda: self._values)

    # Also support the `.all()` used by `_load_scan_cursor`
    def all(self):
        return self._values


class _ScalarOneOrNoneResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


def _playbook(tenant_id, *, domain_id=None, playbook_id=None, title="Restart Service"):
    return SimpleNamespace(
        id=playbook_id or uuid4(),
        tenant_id=tenant_id,
        lifecycle_state="approved",
        domain_id=domain_id,
        stable_key=f"pb-{uuid4().hex[:6]}",
        title=title,
    )


def _version(playbook_id, *, steps=None):
    return SimpleNamespace(
        id=uuid4(),
        playbook_id=playbook_id,
        semantic_version="1.0.0",
        published_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
        steps=steps or [{"text": "restart service x"}],
    )


def _evidence(tenant_id, *, domain_id=None, body="Never restart service x during incident response.", title="KB Article"):
    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        id=uuid4(),
        tenant_id=tenant_id,
        domain_id=domain_id,
        evidence_type="kb_article",
        body_text=body,
        title=title,
        ingested_at=now,
        updated_at=now,
    )


# ---------------------------------------------------------------------------
# should_compare_contradiction — unchanged behaviour
# ---------------------------------------------------------------------------


def test_token_overlap_requires_shared_substantive_tokens():
    # Two tokens in common → gate passes
    assert should_compare_contradiction("restart service x", "never restart service x during incident") is True
    # No substantive overlap → gate fails
    assert should_compare_contradiction("restart service x", "unrelated cooking recipe steps") is False


# ---------------------------------------------------------------------------
# _needs_rescan — cursor / staleness logic
# ---------------------------------------------------------------------------


def test_needs_rescan_when_no_prior_scan():
    ev = SimpleNamespace(ingested_at=datetime.now(timezone.utc), updated_at=None)
    assert _needs_rescan(ev, last_scanned_at=None) is True


def test_needs_rescan_when_evidence_newer_than_scan():
    now = datetime.now(timezone.utc)
    ev = SimpleNamespace(
        ingested_at=now - timedelta(hours=2),
        updated_at=now,  # evidence updated after last scan
    )
    # Last scanned 1 hour ago — evidence was updated after that
    assert _needs_rescan(ev, last_scanned_at=now - timedelta(hours=1)) is True


def test_no_rescan_when_already_scanned_recent():
    now = datetime.now(timezone.utc)
    ev = SimpleNamespace(
        ingested_at=now - timedelta(hours=2),
        updated_at=now - timedelta(hours=2),
    )
    # Last scanned 1 hour ago, evidence hasn't been touched since → skip
    assert _needs_rescan(ev, last_scanned_at=now - timedelta(hours=1)) is False


# ---------------------------------------------------------------------------
# End-to-end scan — happy path: one contradiction detected
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_contradiction_detected_with_embedding_prefilter():
    """A candidate from the embedding top-K passes token overlap → LLM → creates Contradiction."""
    tenant_id = uuid4()
    domain_id = uuid4()
    playbook = _playbook(tenant_id, domain_id=domain_id)
    version = _version(playbook.id)
    evidence = _evidence(tenant_id, domain_id=domain_id)
    added: list = []

    # Sequence of db.execute results matching the new flow:
    #   1. playbooks list
    #   2. latest published version for that playbook
    #   3. scan cursor load (empty — first scan)
    #   4. top-K candidates (vector search)
    #   5. _record_scan_state existing lookup (none yet) — BEFORE LLM call
    #   6. _get_or_create_contradiction existing lookup (none)
    #   7. add_contradicts_edge ensure_edge lookup (none)
    db = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                _ScalarsResult([playbook]),
                _ScalarOneOrNoneResult(version),
                _ScalarsResult([]),              # empty cursor
                _ScalarsResult([evidence]),      # top-K candidates
                _ScalarOneOrNoneResult(None),    # scan_state existing
                _ScalarOneOrNoneResult(None),    # contradiction existing
                _ScalarOneOrNoneResult(None),    # graph edge existing
            ]
        ),
        add=lambda obj: added.append(obj),
        flush=AsyncMock(),
    )

    with (
        patch(
            "contextedge.services.contradiction_service.generate_embedding",
            AsyncMock(return_value=[0.1] * 3072),
        ),
        patch(
            "contextedge.services.contradiction_service.llm_complete_json",
            AsyncMock(return_value={"contradiction": True, "reason": "KB explicitly says never restart"}),
        ),
        patch(
            "contextedge.services.contradiction_service.send_notification",
            AsyncMock(),
        ) as notify_mock,
    ):
        result = await scan_contradictions(db, tenant_id, domain_id=domain_id)

    assert result["contradictions_created"] == 1
    assert result["candidate_pairs_scanned"] == 1
    assert result["llm_calls_used"] == 1
    assert result["budget_exhausted"] is False

    # Persisted artefacts
    assert any(isinstance(obj, Contradiction) for obj in added)
    assert any(isinstance(obj, GraphEdge) for obj in added)
    assert any(isinstance(obj, OperationalEvent) for obj in added)
    # NEW: a scan-state row is always written when a pair is evaluated.
    assert any(isinstance(obj, ContradictionScanState) for obj in added)
    notify_mock.assert_awaited_once()


# ---------------------------------------------------------------------------
# LLM says "no contradiction" — state recorded, no Contradiction created
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_contradiction_still_records_scan_state():
    """Even when the LLM says not-a-contradiction, the scan-state row is
    written so the next incremental scan skips this pair."""
    tenant_id = uuid4()
    domain_id = uuid4()
    playbook = _playbook(tenant_id, domain_id=domain_id)
    version = _version(playbook.id)
    evidence = _evidence(
        tenant_id,
        domain_id=domain_id,
        body="Restart service x after verifying maintenance window.",
    )
    added: list = []

    db = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                _ScalarsResult([playbook]),
                _ScalarOneOrNoneResult(version),
                _ScalarsResult([]),
                _ScalarsResult([evidence]),
                _ScalarOneOrNoneResult(None),   # scan_state existing lookup
            ]
        ),
        add=lambda obj: added.append(obj),
        flush=AsyncMock(),
    )

    with (
        patch(
            "contextedge.services.contradiction_service.generate_embedding",
            AsyncMock(return_value=[0.1] * 3072),
        ),
        patch(
            "contextedge.services.contradiction_service.llm_complete_json",
            AsyncMock(return_value={"contradiction": False, "reason": "Same recommendation"}),
        ),
    ):
        result = await scan_contradictions(db, tenant_id, domain_id=domain_id)

    assert result["contradictions_created"] == 0
    assert result["llm_calls_used"] == 1
    # No Contradiction / GraphEdge — but a scan-state row IS added.
    assert not any(isinstance(obj, Contradiction) for obj in added)
    assert not any(isinstance(obj, GraphEdge) for obj in added)
    assert any(isinstance(obj, ContradictionScanState) for obj in added)


# ---------------------------------------------------------------------------
# Token-overlap gate skip — no LLM call, recorded as skipped_token_overlap
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_token_overlap_gate_skips_llm_call():
    """Embedding picks up a candidate (semantically near) but token overlap
    is too low (e.g. vocabulary mismatch). No LLM call; pair logged as
    skipped_token_overlap."""
    tenant_id = uuid4()
    playbook = _playbook(tenant_id)
    version = _version(playbook.id, steps=[{"text": "restart service x"}])
    evidence = _evidence(
        tenant_id,
        # No substantive token overlap with "restart service x"
        body="recipe for baking croissants with butter and flour",
    )
    added: list = []

    db = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                _ScalarsResult([playbook]),
                _ScalarOneOrNoneResult(version),
                _ScalarsResult([]),
                _ScalarsResult([evidence]),
                _ScalarOneOrNoneResult(None),   # scan_state existing lookup for token-skip path
            ]
        ),
        add=lambda obj: added.append(obj),
        flush=AsyncMock(),
    )

    llm_mock = AsyncMock()
    with (
        patch(
            "contextedge.services.contradiction_service.generate_embedding",
            AsyncMock(return_value=[0.2] * 3072),
        ),
        patch("contextedge.services.contradiction_service.llm_complete_json", llm_mock),
    ):
        result = await scan_contradictions(db, tenant_id)

    assert result["token_skips"] == 1
    assert result["llm_calls_used"] == 0
    llm_mock.assert_not_awaited()
    # A scan-state row is still written for skipped pairs — they're part of
    # the scanned set from the cursor's perspective.
    state_rows = [obj for obj in added if isinstance(obj, ContradictionScanState)]
    assert len(state_rows) == 1
    assert state_rows[0].result == "skipped_token_overlap"


# ---------------------------------------------------------------------------
# Budget cap — stops LLM calls, records remaining as skipped_budget
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_budget_cap_stops_llm_calls_and_records_skipped():
    """Two candidates with overlap. Budget=1. First one goes to LLM,
    second one is recorded as skipped_budget without an LLM call."""
    tenant_id = uuid4()
    playbook = _playbook(tenant_id)
    version = _version(playbook.id)
    ev1 = _evidence(tenant_id, body="Never restart service x — policy prohibits")
    ev2 = _evidence(tenant_id, body="Always restart service x first during outage")
    added: list = []

    db = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                _ScalarsResult([playbook]),
                _ScalarOneOrNoneResult(version),
                _ScalarsResult([]),
                _ScalarsResult([ev1, ev2]),
                # ev1: scan_state existing (None) — will LLM-check
                _ScalarOneOrNoneResult(None),
                # ev1: _get_or_create contradiction existing (None)
                _ScalarOneOrNoneResult(None),
                # ev1: graph edge existing (None)
                _ScalarOneOrNoneResult(None),
                # ev2: scan_state existing lookup (None), hit budget → skip
                _ScalarOneOrNoneResult(None),
            ]
        ),
        add=lambda obj: added.append(obj),
        flush=AsyncMock(),
    )

    llm_calls = 0
    async def llm_side_effect(*args, **kwargs):
        nonlocal llm_calls
        llm_calls += 1
        return {"contradiction": True, "reason": "explicit policy conflict"}

    with (
        patch(
            "contextedge.services.contradiction_service.generate_embedding",
            AsyncMock(return_value=[0.1] * 3072),
        ),
        patch(
            "contextedge.services.contradiction_service.llm_complete_json",
            AsyncMock(side_effect=llm_side_effect),
        ),
        patch(
            "contextedge.services.contradiction_service.send_notification",
            AsyncMock(),
        ),
    ):
        result = await scan_contradictions(db, tenant_id, max_llm_calls=1)

    assert llm_calls == 1
    assert result["llm_calls_used"] == 1
    assert result["budget_skips"] == 1
    assert result["budget_exhausted"] is True
    # Check the budget-skipped pair got a scan-state row.
    budget_rows = [
        obj for obj in added
        if isinstance(obj, ContradictionScanState) and obj.result == "skipped_budget"
    ]
    assert len(budget_rows) == 1


# ---------------------------------------------------------------------------
# Incremental scan — cursor skips already-scanned pairs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cursor_skips_pair_scanned_since_evidence_update():
    """Pair was scanned 1 hour ago; evidence hasn't changed → skip without
    re-running LLM. The biggest LLM-cost saver on warm tenants."""
    tenant_id = uuid4()
    playbook = _playbook(tenant_id)
    version = _version(playbook.id)
    now = datetime.now(timezone.utc)
    evidence = SimpleNamespace(
        id=uuid4(),
        tenant_id=tenant_id,
        domain_id=None,
        evidence_type="kb_article",
        body_text="Never restart service x",
        title="KB Article",
        ingested_at=now - timedelta(hours=2),
        updated_at=now - timedelta(hours=2),   # stable
    )
    # Cursor tuple matches what _load_scan_cursor yields
    cursor_row = (evidence.id, now - timedelta(hours=1))  # scanned 1h ago

    db = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                _ScalarsResult([playbook]),
                _ScalarOneOrNoneResult(version),
                _ScalarsResult([cursor_row]),   # non-empty cursor
                _ScalarsResult([evidence]),
            ]
        ),
        add=Mock(),
        flush=AsyncMock(),
    )

    llm_mock = AsyncMock()
    with (
        patch(
            "contextedge.services.contradiction_service.generate_embedding",
            AsyncMock(return_value=[0.1] * 3072),
        ),
        patch("contextedge.services.contradiction_service.llm_complete_json", llm_mock),
    ):
        result = await scan_contradictions(db, tenant_id)

    assert result["cursor_skips"] == 1
    assert result["llm_calls_used"] == 0
    llm_mock.assert_not_awaited()
    # No state row added — the existing row is untouched.
    db.add.assert_not_called()


# ---------------------------------------------------------------------------
# Defaults exposed for caller configurability
# ---------------------------------------------------------------------------


def test_default_budget_and_top_k_are_sensible():
    # 1000 LLM calls × ~$0.5/M tokens × few-hundred tokens per call = ~$0.50/beat ceiling.
    assert DEFAULT_SCAN_BUDGET == 1000
    # 20 candidates per step is wide enough to catch cross-vocab contradictions,
    # narrow enough to bound per-step cost.
    assert DEFAULT_TOP_K_CANDIDATES == 20
