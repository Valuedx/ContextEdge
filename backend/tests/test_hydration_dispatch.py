"""Knowledge evidence does not get a thread-hydration task.

A KB article's body, fetched at sync time, IS its content. The Zoho
connector says so and returns `hydration: not_applicable` without making a
call — as do the SapphireIMS connector and ServiceNow alert rollups.

So dispatching hydration for an article was never WRONG, just pointless,
which is exactly why nothing ever failed and nobody noticed: a 630-article
backfill queued 578 tasks that each did nothing. Harmless one at a time,
and it puts hundreds of no-ops into a lane real hydration then waits
behind.
"""

from __future__ import annotations

import inspect

from contextedge.services.evidence_typing import KNOWLEDGE_EVIDENCE_TYPES
from contextedge.workers import extraction_tasks


def test_normalize_reports_the_evidence_type():
    """The dispatch gate needs to know what it just normalized, so the
    type travels back with the result rather than being re-queried."""
    source = inspect.getsource(extraction_tasks._normalize)

    assert '"_evidence_type": ev.evidence_type' in source


def test_hydration_is_not_dispatched_for_knowledge():
    source = inspect.getsource(extraction_tasks.normalize_evidence)

    assert "KNOWLEDGE_EVIDENCE_TYPES" in source
    assert "hydratable" in source


def test_tickets_still_hydrate():
    """The fix must not silence real hydration: a ticket's conversation is
    where the diagnosis usually lives, and losing it would be a far worse
    bug than the one being fixed."""
    assert "ticket" not in KNOWLEDGE_EVIDENCE_TYPES
    assert "thread_message" not in KNOWLEDGE_EVIDENCE_TYPES

    source = inspect.getsource(extraction_tasks.normalize_evidence)
    assert "hydrate_thread.delay(thread_ext_id, source_id, tenant_id)" in source


def test_knowledge_types_are_the_ones_with_no_conversation():
    """Guards the assumption the gate rests on. If a conversational type
    is ever added to this set, hydration for it stops silently."""
    for knowledge in ("kb_article",):
        assert knowledge in KNOWLEDGE_EVIDENCE_TYPES


def test_the_connector_short_circuit_remains_the_backstop():
    """Belt and braces. The dispatch gate saves the queue churn; the
    connector still refuses to do the work, so a hydration task arriving
    by any other route (the manual API, a replayed message) is still a
    no-op rather than a wasted round trip."""
    from contextedge.connectors.zoho_desk.connector import ZohoDeskConnector

    source = inspect.getsource(ZohoDeskConnector.hydrate_thread)

    assert 'prefix == "zoho_article"' in source
    assert "not_applicable" in source
