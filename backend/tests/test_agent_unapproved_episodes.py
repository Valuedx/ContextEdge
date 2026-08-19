"""Unapproved episode drafts reach the agent as labelled reference material.

The reviewer queue lags ingestion, so hiding every unapproved episode meant
the agent could not see this week's incident while answering about it. They
are admitted — but a draft nobody checked must never read like settled
precedent, so it is labelled at hydration, carries an explicit caveat, is
capped in its own seed allocation, and is relevance-discounted so it cannot
evict a reviewed episode.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from contextedge.graph.agent.contracts import AgentGraphAccessScope
from contextedge.graph.agent.hydrators import (
    UNAPPROVED_EPISODE_CAVEAT,
    hydrate_node,
    node_is_visible,
)
from contextedge.graph.agent.repository import (
    UNAPPROVED_EPISODE_SEED_LIMIT,
    UNAPPROVED_SEED_RELEVANCE_FACTOR,
)


def _scope(tenant_id):
    return AgentGraphAccessScope(
        tenant_id=tenant_id, principal_id=uuid4(), principal_type="user"
    )


def _episode(tenant_id, reviewer_state):
    return SimpleNamespace(
        id=uuid4(),
        tenant_id=tenant_id,
        domain_id=None,
        workspace_id=None,
        title="VPN gateway rejecting all logins",
        root_cause_summary="Expired RADIUS certificate",
        final_outcome="Renewed the certificate",
        status=reviewer_state,
        reviewer_state=reviewer_state,
        extraction_confidence=0.9,
        contradictions=None,
    )


def test_pending_review_episode_is_visible_to_the_agent():
    """The change this module exists for: a draft is reference material,
    not something to withhold."""
    tenant_id = uuid4()
    episode = _episode(tenant_id, "pending_review")

    assert node_is_visible("episode", episode, _scope(tenant_id), set()) is True


def test_approved_episode_remains_visible():
    tenant_id = uuid4()
    episode = _episode(tenant_id, "approved")

    assert node_is_visible("episode", episode, _scope(tenant_id), set()) is True


def test_superseded_episode_stays_hidden():
    """Superseded is the state a merge gives the LOSER, and the corpus
    holds far more of them than live episodes. Admitting them would bury
    the agent in stale near-copies that read as corroboration."""
    tenant_id = uuid4()
    episode = _episode(tenant_id, "superseded")

    assert node_is_visible("episode", episode, _scope(tenant_id), set()) is False


def test_unapproved_episode_is_labelled_and_carries_the_caveat():
    """`reviewer_state` was already projected and was not enough: a bare
    enum among a dozen sibling facts is not a warning."""
    episode = _episode(uuid4(), "pending_review")

    projected = hydrate_node("episode", episode)

    assert projected.label.startswith("[UNAPPROVED DRAFT]")
    assert "VPN gateway rejecting all logins" in projected.label
    assert projected.facts["agent_caveat"] == UNAPPROVED_EPISODE_CAVEAT
    assert "reference only" in projected.facts["agent_caveat"]
    # The raw state stays too — the caveat explains it, it does not replace it.
    assert projected.facts["reviewer_state"] == "pending_review"


def test_approved_episode_gets_no_caveat_and_no_label_prefix():
    """The warning must mean something: if every episode carried it, it
    would be noise the agent learns to skip."""
    episode = _episode(uuid4(), "approved")

    projected = hydrate_node("episode", episode)

    assert projected.label == "VPN gateway rejecting all logins"
    assert "agent_caveat" not in projected.facts


def test_draft_seeds_rank_below_approved_seeds_at_equal_similarity():
    """Relevance is pure embedding distance, which knows nothing about
    whether anyone checked the episode. Without the discount, a draft that
    merely reads more like the query outranks reviewed precedent."""
    similarity = 0.9
    approved_relevance = round(0.6 + 0.3 * similarity, 4)
    draft_relevance = round(
        (0.6 + 0.3 * similarity) * UNAPPROVED_SEED_RELEVANCE_FACTOR, 4
    )

    assert draft_relevance < approved_relevance
    # And the discount must outweigh the whole similarity spread, so that a
    # perfectly-matching draft still loses to a barely-admitted approved one.
    best_draft = round((0.6 + 0.3 * 1.0) * UNAPPROVED_SEED_RELEVANCE_FACTOR, 4)
    worst_approved = round(0.6 + 0.3 * 0.5, 4)
    assert best_draft < worst_approved


def test_draft_seed_allocation_is_small_and_separate():
    """Drafts get their own slots so they supplement the three approved
    ones rather than competing for them."""
    assert 0 < UNAPPROVED_EPISODE_SEED_LIMIT < 3
