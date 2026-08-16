"""ServiceNow owns the knowledge lifecycle; ContextEdge has to respect it.

The connector fetched `workflow_state` on `kb_knowledge` from the day it was
written, and the field appeared exactly once in the codebase — in the list of
fields to fetch. Drafts nobody approved, articles in review, and ones a human
explicitly retired were retrieved and cited identically to published ones.

The tests split in two: what must be withheld, and — just as important — what
must NOT be, because a lifecycle rule that over-reaches empties a corpus.
"""

import uuid
from types import SimpleNamespace

import pytest

from contextedge.graph.agent.hydrators import node_is_visible
from contextedge.models.evidence import EvidenceItem
from contextedge.services.knowledge_lifecycle import (
    KNOWLEDGE_STATES,
    WITHHELD_KNOWLEDGE_STATES,
    current_knowledge_clause,
    derive_knowledge_state,
    is_current,
)


def _payload(**kwargs):
    return {"_connector_source_type": "servicenow",
            "_connector_object_type": "kb_knowledge", **kwargs}


# =========================================================================
# Reading the source's answer
# =========================================================================


def test_servicenow_states_are_read_from_the_payload():
    for raw, expected in (
        ("draft", "draft"),
        ("review", "review"),
        ("published", "published"),
        ("retired", "retired"),
        ("Published", "published"),   # instances vary in case
        ("  RETIRED ", "retired"),
    ):
        assert derive_knowledge_state(_payload(workflow_state=raw)) == expected


def test_an_unrecognised_state_is_unknown_rather_than_withheld():
    """Withholding on a value we do not understand is the more damaging of
    the two mistakes: it removes guidance nobody asked to remove."""
    assert derive_knowledge_state(_payload(workflow_state="awaiting_legal")) is None
    assert is_current(None) is True


def test_pending_retirement_still_serves():
    """On its way out is not out. The article is what a responder has until
    somebody actually retires it."""
    assert derive_knowledge_state(_payload(workflow_state="pending_retirement")) == "published"


def test_a_source_with_no_lifecycle_reports_nothing():
    """Most knowledge has no lifecycle at all — a file-share SOP, an upload."""
    assert derive_knowledge_state({"_connector_source_type": "sharepoint"}) is None
    assert derive_knowledge_state({}) is None
    assert derive_knowledge_state(None) is None


def test_a_servicenow_record_with_no_state_field_reports_nothing():
    assert derive_knowledge_state(_payload()) is None


# =========================================================================
# What that answer does
# =========================================================================


def test_unapproved_and_withdrawn_articles_are_withheld():
    for state in ("draft", "review", "retired"):
        assert is_current(state) is False


def test_published_and_unknown_are_served():
    assert is_current("published") is True
    assert is_current(None) is True


def test_the_withheld_set_is_a_subset_of_the_vocabulary():
    assert set(WITHHELD_KNOWLEDGE_STATES) < set(KNOWLEDGE_STATES)
    assert "published" not in WITHHELD_KNOWLEDGE_STATES


def test_the_sql_form_keeps_null_rows():
    """SQL's three-valued logic drops NULLs from a NOT IN, which would
    withhold exactly the articles whose source never had a lifecycle — the
    majority of the corpus."""
    clause = str(current_knowledge_clause(EvidenceItem).compile(
        compile_kwargs={"literal_binds": True}
    ))
    assert "IS NULL" in clause
    assert "NOT IN" in clause


# =========================================================================
# The agent must not be handed one
# =========================================================================


def _scope(tenant_id):
    return SimpleNamespace(
        tenant_id=tenant_id, domain_id=None, allowed_domain_ids=None,
        workspace_ids=(), playbook_risk_cap="high",
    )


def _evidence(tenant_id, knowledge_state=None):
    return SimpleNamespace(
        tenant_id=tenant_id, domain_id=None, workspace_id=None,
        knowledge_state=knowledge_state, sensitivity_label=None,
        redaction_status=None, access_policy_id=None,
    )


@pytest.mark.parametrize("state", ["draft", "review", "retired"])
def test_a_withheld_article_never_reaches_the_agent(state):
    tenant_id = uuid.uuid4()
    visible = node_is_visible(
        "evidence", _evidence(tenant_id, state), _scope(tenant_id), set()
    )
    assert visible is False


@pytest.mark.parametrize("state", [None, "published"])
def test_a_current_article_still_reaches_the_agent(state):
    tenant_id = uuid.uuid4()
    visible = node_is_visible(
        "evidence", _evidence(tenant_id, state), _scope(tenant_id), set()
    )
    assert visible is True


def test_a_ticket_is_unaffected():
    """Non-knowledge evidence carries no state, and the gate has to be inert
    for it — otherwise this rule quietly filters the incident record too."""
    tenant_id = uuid.uuid4()
    ticket = _evidence(tenant_id, knowledge_state=None)
    assert node_is_visible("evidence", ticket, _scope(tenant_id), set()) is True


def test_a_retirement_is_picked_up_without_the_body_changing():
    """The content hash covers the BODY, and retiring an article does not
    rewrite it — so a state change lands on the existing row rather than
    creating a new one. If the re-ingest path did not refresh the state, a
    retired article would stay published forever."""
    import inspect

    from contextedge.workers import extraction_tasks

    source = inspect.getsource(extraction_tasks._normalize)
    existing_branch = source[source.index("if existing:"):]
    assert "derive_knowledge_state(payload)" in existing_branch
    # And never un-retires on a payload that stopped carrying the field.
    assert "refreshed_state is not None" in existing_branch


# =========================================================================
# Retrieval and supersession honour the same rule
# =========================================================================


def test_retrieval_withholds_rather_than_demotes():
    """F4b demotes a superseded article because a filename heuristic guessed.
    Here a human used their own system to say it is not current, and serving
    it anyway — even ranked last — overrides that decision."""
    import inspect

    from contextedge.services import knowledge_retrieval_service as retrieval

    source = inspect.getsource(retrieval._retrieve)
    assert "is_current(" in source
    assert "withheld += 1" in source
    # And says so: an empty result that is really "all of it is retired" is a
    # knowledge gap somebody should act on, not an absence of guidance.
    assert "withheld_by_source_lifecycle" in inspect.getsource(retrieval)


def test_supersession_ignores_what_the_source_already_withdrew():
    import inspect

    from contextedge.services import knowledge_supersession_service as supersession

    assert "is_current(" in inspect.getsource(supersession.propose_supersessions)
    assert "current_knowledge_clause" in inspect.getsource(supersession.scan_tenant_knowledge)


def test_the_projection_seed_query_filters_too():
    """Belt and braces, matching the legal-hold precedent immediately above
    it: a withheld document must not consume a seed slot either."""
    import inspect

    from contextedge.graph.agent import repository

    assert "current_knowledge_clause(EvidenceItem)" in inspect.getsource(repository)
