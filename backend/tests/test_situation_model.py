"""Operational situations: the invariants that keep a situation honest.

A situation is what is happening NOW. It is not an episode (which needs a
resolution to reconstruct), not a knowledge case (which is what a document
claims), and not a renamed correlation edge — an edge says two pieces of
evidence look related, a situation says many signals describe ONE
occurrence, which is a stronger claim.

These tests pin the properties that stop that claim degrading: causality
that cannot run backwards, merges that cannot lose their target, healthy
evidence that cannot be read without its age, and idempotency so a retry
cannot invent a second occurrence.
"""

from __future__ import annotations

from contextedge.models import (
    CHANGE_CANDIDATE_STATUSES,
    EVIDENCE_ROLES,
    IMPACT_ROLES,
    MEMBERSHIP_STATUSES,
    SITUATION_STATES,
    OperationalSituation,
    SituationChangeCandidate,
    SituationEntityImpact,
    SituationEvidenceMembership,
)


def _cols(model):
    return set(model.__table__.columns.keys())


def _constraint(model, name):
    return next((c for c in model.__table__.constraints if c.name == name), None)


def _index(model, name):
    return next((i for i in model.__table__.indexes if i.name == name), None)


# --- causality ------------------------------------------------------------


def test_a_change_after_onset_cannot_be_causal():
    """Time only runs one way. A change made at 10:20 cannot have caused an
    outage that began at 10:02 — it can be remediation, a rollback or a
    diagnostic action, and the database refuses to record it as the cause.

    Verified live against Postgres, not only asserted here."""
    check = _constraint(SituationChangeCandidate, "ck_change_after_onset_not_causal")

    assert check is not None
    expression = str(check.sqltext)
    assert "after_onset" in expression
    for causal in ("suspected", "corroborated", "confirmed"):
        assert causal in expression


def test_change_lifecycle_separates_candidate_from_confirmed():
    """`confirmed` must be reachable only from governed evidence — an ITSM
    caused-by relation, an approved RCA, a human decision — never from a
    score. The vocabulary keeps them distinct so a ranking cannot be
    mistaken for a verdict."""
    assert "confirmed" in CHANGE_CANDIDATE_STATUSES
    assert "suspected" in CHANGE_CANDIDATE_STATUSES
    # Remediation is its own outcome, not a weaker kind of cause.
    assert "remediation" in CHANGE_CANDIDATE_STATUSES
    assert "rollback" in CHANGE_CANDIDATE_STATUSES


def test_change_candidate_carries_its_reasoning():
    """"Why is CHG871 relevant" has to be answerable. A bare score is not
    an answer, so the decomposition and a human-readable summary are part
    of the row, and the basis for a confirmation is recorded separately
    from the score that only ranked it."""
    columns = _cols(SituationChangeCandidate)

    for expected in (
        "score_breakdown",
        "reason_summary",
        "confirmation_basis",
        "temporal_relation",
        "minutes_from_onset",
        "topology_distance",
        "rejection_reason",
    ):
        assert expected in columns, expected


def test_correlation_score_is_not_named_like_a_probability():
    """0.86 means "strong under the current model", never "86% likely to
    be the cause". The column name is the first place that distinction
    gets lost."""
    columns = _cols(SituationChangeCandidate)

    assert "correlation_score" in columns
    for probabilistic in ("probability", "likelihood", "causal_probability"):
        assert probabilistic not in columns


# --- merge and lifecycle --------------------------------------------------


def test_a_merged_situation_must_name_its_survivor():
    """Merged rows are never deleted — they are how "why did these two
    become one" stays answerable — so a merged row that lost its pointer
    would be an orphan nobody could explain."""
    check = _constraint(OperationalSituation, "ck_situation_merged_has_target")

    assert check is not None
    assert "merged" in str(check.sqltext)


def test_lifecycle_distinguishes_quiet_from_resolved():
    """Absence of signal is not recovery. `stabilizing` and `resolved` are
    separate states so "nothing new arrived" can never be recorded as
    "verified fixed"."""
    for state in ("emerging", "active", "stabilizing", "resolved", "reopened"):
        assert state in SITUATION_STATES
    # merged and invalidated are terminal dispositions, not health states.
    assert "merged" in SITUATION_STATES
    assert "invalidated" in SITUATION_STATES


def test_fingerprint_is_not_unique():
    """A fingerprint suppresses duplicate creation and finds candidates; it
    is NOT identity. The same service can fail twice in one window for
    unrelated reasons, and a unique constraint here would silently merge
    two real occurrences."""
    assert "fingerprint" in _cols(OperationalSituation)

    unique_indexes = [i for i in OperationalSituation.__table__.indexes if i.unique]
    for index in unique_indexes:
        assert "fingerprint" not in {c.name for c in index.columns}


# --- membership -----------------------------------------------------------


def test_membership_is_not_binary():
    """Evidence can sit provisionally in more than one candidate situation
    until something decides. Forcing an early choice is how a false merge
    becomes permanent, and a false merge is worse than a missed one."""
    for status in ("confirmed", "inferred", "provisional", "rejected", "retired"):
        assert status in MEMBERSHIP_STATUSES


def test_rejected_memberships_are_kept_not_deleted():
    """The machine score beside the human verdict is the only record of
    what the model got wrong — the calibration dataset. A delete would
    throw it away."""
    columns = _cols(SituationEvidenceMembership)

    for expected in ("review_status", "review_reason", "reviewed_by", "reviewed_at"):
        assert expected in columns
    assert "score_breakdown" in columns
    assert "machine_decision_version" in columns


def test_membership_records_source_lineage():
    """A monitoring alert, the ticket it opened and the mail it sent are
    three records of ONE observation. Without a lineage group, confidence
    counts them as three independent confirmations — while three separate
    monitoring systems agreeing genuinely is three, and should count for
    more."""
    assert "source_lineage_group" in _cols(SituationEvidenceMembership)


def test_membership_is_idempotent_per_evidence():
    """Retry safety: evaluating the same evidence twice must update one row
    rather than inventing a second membership."""
    index = _index(SituationEvidenceMembership, "uq_situation_membership")

    assert index is not None and index.unique
    assert {c.name for c in index.columns} == {"situation_id", "evidence_id"}


def test_evidence_roles_cover_signals_and_remediation():
    for role in (
        "primary_incident",
        "related_incident",
        "monitoring_alert",
        "change_candidate",
        "remediation_change",
        "recovery_signal",
    ):
        assert role in EVIDENCE_ROLES


# --- impact, including what is fine ---------------------------------------


def test_healthy_control_is_a_first_class_impact_role():
    """What appears healthy narrows a diagnosis as much as what is broken:
    a failing order API with a healthy database rules out a whole branch of
    hypotheses."""
    assert "healthy_control" in IMPACT_ROLES
    assert "suspected_root_component" in IMPACT_ROLES
    assert "shared_dependency" in IMPACT_ROLES


def test_impact_carries_signal_freshness():
    """"Database healthy" is useful at two minutes old and dangerous at
    eight hours. The claim has to carry its own age or a reader cannot tell
    those apart — and for a healthy control that difference decides whether
    a hypothesis is eliminated or merely unmeasured."""
    columns = _cols(SituationEntityImpact)

    assert "signal_observed_at" in columns
    assert "basis" in columns
    assert "topology_distance" in columns


def test_impact_allows_one_entity_in_several_roles():
    """An entity can be both affected and a shared dependency; the role is
    part of the identity so recording one does not overwrite the other."""
    index = _index(SituationEntityImpact, "uq_situation_entity_impact")

    assert index is not None and index.unique
    assert {c.name for c in index.columns} == {
        "situation_id",
        "entity_id",
        "impact_role",
    }


# --- separation from neighbouring objects ---------------------------------


def test_a_situation_is_not_an_episode():
    """A situation may exist while nothing is resolved. It must not carry
    the empirical outcome fields an episode has, or it will be read as one
    — and an episode needs a resolution to reconstruct, which an ongoing
    occurrence does not have."""
    columns = _cols(OperationalSituation)

    for forbidden in ("final_outcome", "root_cause_summary", "reviewer_state"):
        assert forbidden not in columns


def test_confidences_are_kept_separate():
    """situation_confidence, membership_confidence, impact confidence and
    change correlation_score answer different questions. One shared number
    would make every reader guess which."""
    assert "situation_confidence" in _cols(OperationalSituation)
    assert "membership_confidence" in _cols(SituationEvidenceMembership)
    assert "confidence" in _cols(SituationEntityImpact)
    assert "correlation_score" in _cols(SituationChangeCandidate)


def test_scoring_stays_explainable_after_the_weights_change():
    """A situation scored last month must stay explainable when the model
    changes, so the version that produced it travels with it."""
    assert "correlation_version" in _cols(OperationalSituation)
    assert "machine_decision_version" in _cols(SituationEvidenceMembership)
