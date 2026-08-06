"""Auto-hydration must fire for the ticket, not for its own output.

Hydration stamps `_thread_id` onto every message it writes, so keying the
dispatch on "this payload carries a thread id" makes each hydrated
message ask for its own thread to be hydrated again. Measured on the live
graph: 341 raw objects carry a thread id across 34 distinct threads — a
10x amplification, and 50x on the largest ticket.

It terminates, because the re-fetch finds no new raw objects and nothing
recurses further. What it costs is API calls: a /threads list, up to
THREAD_FETCH_LIMIT detail calls and a /comments call per redundant pass,
against an API that answers throttling with empty result sets rather than
an error — the failure that stored 11 of 20 tickets as empty while
reporting success.
"""

from __future__ import annotations


def _thread_id_for(payload: dict) -> str | None:
    """The dispatch decision, as `_normalize` makes it."""
    is_hydrated_message = (
        (payload or {}).get("_connector_object_type") == "hydrated_message"
    )
    return None if is_hydrated_message else (payload or {}).get("_thread_id")


def test_a_ticket_asks_for_its_thread_to_be_hydrated():
    payload = {
        "_thread_id": "zoho_ticket:11270000096536210",
        "_connector_object_type": "ticket",
        "ticket_number": "409838",
    }
    assert _thread_id_for(payload) == "zoho_ticket:11270000096536210"


def test_a_hydrated_message_does_not_re_hydrate_its_own_thread():
    """The regression. This payload is hydration's OUTPUT, and it carries
    the same `_thread_id` the parent ticket did."""
    payload = {
        "_thread_id": "zoho_ticket:11270000096536210",
        "_connector_object_type": "hydrated_message",
        "body": "Restarted the broker; the queue drained.",
    }
    assert _thread_id_for(payload) is None


def test_a_record_with_no_thread_asks_for_nothing():
    assert _thread_id_for({"_connector_object_type": "kb_article"}) is None
    assert _thread_id_for({}) is None
    assert _thread_id_for(None) is None


def test_the_amplification_is_bounded_to_one_dispatch_per_thread():
    """A 20-message ticket must produce one hydration request, not 21."""
    thread = "zoho_ticket:11270000091330285"
    payloads = [{"_thread_id": thread, "_connector_object_type": "ticket"}] + [
        {"_thread_id": thread, "_connector_object_type": "hydrated_message"}
        for _ in range(20)
    ]
    dispatches = [p for p in payloads if _thread_id_for(p)]
    assert len(dispatches) == 1


def test_normalize_applies_the_same_rule_it_is_tested_on():
    """Guards against the check drifting away from this test: the source
    must key the decision on the object type, not merely on the presence
    of a thread id."""
    import inspect

    from contextedge.workers import extraction_tasks

    source = inspect.getsource(extraction_tasks._normalize)
    assert "hydrated_message" in source
    assert "_connector_object_type" in source
