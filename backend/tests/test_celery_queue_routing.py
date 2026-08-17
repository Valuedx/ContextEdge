"""Queue routing: which lane each task lands in.

Routing is the only thing standing between a bulk ingest and a starved
graph. `normalize_evidence` -> `correlate_evidence` -> `reconstruct_episode`
is the chain that builds the context graph, and every link used to route to
the same `extraction` queue that normalization floods. Because thread
hydration turns one ticket into ~41 more normalize tasks, that queue grows
while it is being drained: measured on the live Zoho backfill it was 8,255
deep and rising by ~70/minute, the head was 60/60 normalize, and correlation
had been dispatched without ever being received. Episodes stayed at zero.

These assertions are cheap and the failure they prevent is invisible — the
pipeline reports success the whole time it is failing to build a graph.
"""

import pytest

from contextedge.workers.celery_app import celery_app


def _queue_for(task_name: str) -> str:
    """Resolve a task name through the configured routes, in order."""
    routes = celery_app.conf.task_routes
    for pattern, dest in routes.items():
        if pattern.endswith(".*"):
            if task_name.startswith(pattern[:-1]):
                return dest["queue"]
        elif pattern == task_name:
            return dest["queue"]
    return celery_app.conf.task_default_queue


@pytest.mark.parametrize(
    "task_name",
    [
        "extraction.correlate_evidence",
        "extraction.reconstruct_episode",
        "extraction.compute_evidence_baseline",
    ],
)
def test_graph_chain_does_not_share_the_bulk_ingest_queue(task_name):
    """The graph-building chain must not queue behind its own producer."""
    assert _queue_for(task_name) == "correlation"


@pytest.mark.parametrize(
    "task_name",
    ["extraction.chunk_evidence", "extraction.embed_chunks_batch"],
)
def test_retrieval_chain_does_not_share_the_bulk_ingest_queue(task_name):
    """An unembedded chunk is invisible to vector search.

    Same starvation as the graph chain, worse symptom: the evidence is
    ingested and reports success while being unretrievable.
    """
    assert _queue_for(task_name) == "embedding"


def test_bulk_normalization_still_uses_the_extraction_queue():
    assert _queue_for("extraction.normalize_evidence") == "extraction"


def test_a_broker_blip_does_not_kill_a_worker():
    """A connection reset must pause a worker, never terminate it.

    The broker here is reached through WSL's port relay, which drops TCP
    connections intermittently under load. With Celery's defaults that
    raises OperationalError and the worker exits — four of eight died to one
    blip on 2026-08-17 and throughput halved with nothing reporting a
    failure. Silent capacity loss reads as "healthy but slow".
    """
    conf = celery_app.conf
    assert conf.broker_connection_retry is True
    assert conf.broker_connection_retry_on_startup is True
    # None = retry forever. A finite cap turns a long outage into a dead pool.
    assert conf.broker_connection_max_retries is None
    assert conf.broker_transport_options.get("socket_keepalive") is True


def test_relevance_gate_keeps_its_fast_lane():
    # The precedent this fix follows — do not let a later edit undo it.
    assert _queue_for("extraction.classify_relevance") == "default"


def test_specific_routes_are_declared_before_the_wildcard():
    """Order matters: dict order decides which pattern wins.

    `extraction.*` would swallow every one of the specific routes above if
    it were declared first, and the symptom would be silent — tasks run,
    nothing errors, the graph simply never forms.
    """
    keys = list(celery_app.conf.task_routes)
    wildcard = keys.index("extraction.*")
    for specific in (
        "extraction.classify_relevance",
        "extraction.correlate_evidence",
        "extraction.reconstruct_episode",
        "extraction.compute_evidence_baseline",
        "extraction.chunk_evidence",
        "extraction.embed_chunks_batch",
    ):
        assert keys.index(specific) < wildcard, f"{specific} must precede extraction.*"
