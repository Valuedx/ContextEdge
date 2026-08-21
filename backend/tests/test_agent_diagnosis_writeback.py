"""F1: agent diagnoses flow back, and cannot become their own evidence.

The loop this closes is the one the roadmap calls the biggest structural
omission — without it every diagnosis starts from zero and the graph learns
only from human tickets, never from the agent runs it exists to support.

The hazard it opens is the reason it stayed unbuilt: an agent that reads its own
unreviewed conclusions as evidence launders opinion into fact and grows more
confident every lap. The projection already refuses this — `hydrators` drops any
decision with `actor_type='ai'` and `status='pending'` — and F1 depends on that
guard rather than restating it, so these tests pin both ends: the guard is still
there, and the F1 retrieval honours it instead of quietly routing around it.

Exercised live before these were written: a diagnosis with three hypotheses (two
rejected) was invisible to the retrieval while pending, visible to a review
surface, and inherited by the next reader only once an outcome moved it off
pending.
"""

from __future__ import annotations

import inspect

from contextedge.graph.agent import hydrators
from contextedge.integrations.maf import client as maf_client
from contextedge.services import agent_diagnosis_service as ads
from contextedge.services.agent_diagnosis_service import Hypothesis

# --- the containment -------------------------------------------------------


def test_a_diagnosis_is_written_pending_and_ai_authored():
    """Both halves matter: the projection's guard keys on the pair, so
    writing either one differently silently un-contains the record."""
    source = inspect.getsource(ads.record_agent_diagnosis)
    assert 'actor_type="ai"' in source
    assert "status=INITIAL_STATUS" in source
    assert ads.INITIAL_STATUS == "pending"


def test_the_projection_still_drops_pending_ai_decisions():
    """F1 relies on this guard living upstream. If it is ever relaxed,
    agent write-back becomes agent self-training on the same day."""
    source = inspect.getsource(hydrators)
    assert 'obj.actor_type == "ai" and obj.status == "pending"' in source


def test_prior_hypotheses_excludes_unreviewed_ai_by_default():
    """Honoured explicitly rather than inherited by accident — this is a
    different code path from the projection and would not be covered by it."""
    source = inspect.getsource(ads.prior_hypotheses)
    assert "if not include_unreviewed:" in source
    assert 'Decision.actor_type != "ai"' in source
    assert 'Decision.status != "pending"' in source


def test_human_authored_decisions_are_not_excluded_while_pending():
    """A person writing a decision IS the review. Filtering them out would
    hide the most trustworthy records in the store."""
    source = inspect.getsource(ads.prior_hypotheses)
    # The condition is an OR: not-ai OR not-pending.
    assert '(Decision.actor_type != "ai") | (Decision.status != "pending")' in source


def test_the_agent_client_cannot_ask_for_unreviewed_diagnoses():
    """The cheapest guarantee an agent will not read pending conclusions is
    to give it no argument that would. The in-process port never passes
    include_unreviewed."""
    source = inspect.getsource(maf_client.InProcessDiagnosisClient.prior)
    assert "include_unreviewed" not in source


# --- what gets recorded ----------------------------------------------------


def test_a_rejected_hypothesis_carries_its_reason():
    """A hypothesis dropped without a reason teaches the next agent nothing
    and is indistinguishable from one never seriously considered."""
    option = Hypothesis(
        "RADIUS backend unreachable",
        selected=False,
        rejection_reason="radius-auth-01 answered test binds throughout the window",
        rejection_code="disproven_by_evidence",
    ).as_option()
    assert option["selected"] is False
    assert option["rejection_reason"]
    assert option["rejection_code"] == "disproven_by_evidence"


def test_the_selected_hypothesis_carries_no_rejection():
    """A chosen option with a rejection reason is a contradiction, and would
    read downstream as both taken and refused."""
    option = Hypothesis("Intermediate CA rejected", selected=True, confidence=0.86).as_option()
    assert option["selected"] is True
    assert option["rejection_reason"] is None
    assert option["rejection_code"] is None
    assert option["suitability"] == 0.86


def test_a_rejection_reason_on_a_selected_hypothesis_is_dropped_not_kept():
    """Defensive: a caller that sets both should not produce a row asserting
    both."""
    option = Hypothesis(
        "chosen anyway", selected=True, rejection_reason="should not survive"
    ).as_option()
    assert option["rejection_reason"] is None


def test_hypotheses_are_capped():
    """This is model output; the count is not bounded by anything upstream."""
    assert ads.MAX_HYPOTHESES <= 20
    source = inspect.getsource(ads.record_agent_diagnosis)
    assert "[:MAX_HYPOTHESES]" in source


# --- closing the loop ------------------------------------------------------


def test_an_outcome_is_what_makes_a_diagnosis_usable():
    """Not ageing. An unverified conclusion must not become the next agent's
    premise merely by getting old."""
    source = inspect.getsource(ads.record_diagnosis_outcome)
    assert "record_outcome" in source
    doc = ads.record_diagnosis_outcome.__doc__ or ""
    assert "pending" in doc


def test_the_agent_tool_tells_the_agent_the_record_is_inert():
    """The tool description is the only place the agent learns that writing
    back is not the same as publishing."""
    from contextedge.integrations.maf.tools import DiagnosisTools

    description = str(
        getattr(DiagnosisTools.record_diagnosis, "description", "")
        or (DiagnosisTools.record_diagnosis.__doc__ or "")
    )
    lowered = description.lower()
    assert "review" in lowered or "pending" in lowered
