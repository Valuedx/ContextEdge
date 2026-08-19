"""Knowledge cases are a separate object, and only episodes are empirical.

A KB article reconstructed into an episode asserts something happened. It
did not: a document claims a resolution works. The split exists so that
claim can never be counted as an observation, and these tests pin the
properties that keep it that way.
"""

from __future__ import annotations

from contextedge.models import KnowledgeCase, KnowledgeCaseStep, PatternEvidence


def _cols(model):
    return set(model.__table__.columns.keys())


def test_knowledge_case_is_its_own_table():
    """Not a discriminator on episodes. A `kind` column would make every
    query that counts, clusters, scores or cites episodes silently wrong
    until someone remembered `AND kind = 'observed'`; a separate table
    turns that same mistake into a missing join, which is loud."""
    assert KnowledgeCase.__tablename__ == "knowledge_cases"
    assert KnowledgeCaseStep.__tablename__ == "knowledge_case_steps"


def test_knowledge_case_carries_no_empirical_outcome():
    """How well a documented resolution actually works belongs to the
    pattern it supports, measured from episodes. Storing it here would
    re-blur the provenance the split exists to keep — KC-17 stays
    permanently 'documentation said this'; the PATTERN graduates."""
    columns = _cols(KnowledgeCase)

    for forbidden in (
        "final_outcome",
        "outcome",
        "empirical_confidence",
        "reopen_count",
        "resolution_duration",
        "occurred_at",
        "reviewer_state",
    ):
        assert forbidden not in columns, forbidden


def test_knowledge_case_keeps_the_reusable_semantics():
    """The reconstruction is the valuable part and is not discarded: a KB
    article is often the only structured description of a failure mode
    nobody has hit yet."""
    columns = _cols(KnowledgeCase)

    for expected in (
        "symptom_summary",
        "documented_cause",
        "documented_resolution",
        "validation_guidance",
        "entity_refs",
        "applicability",
        "embedding",
    ):
        assert expected in columns, expected


def test_documented_language_not_observed_language():
    """`documented_cause`, never `root_cause`. The source asserts it;
    nobody confirmed it here, and the column name is where that slips."""
    columns = _cols(KnowledgeCase)

    assert "documented_cause" in columns
    assert "root_cause_summary" not in columns


def test_knowledge_case_step_has_no_outcome_flags():
    """An episode step records what happened — failed_flag,
    successful_flag, result_state. A documented step describes an action
    to take. Adding an outcome field here is how the distinction erodes."""
    columns = _cols(KnowledgeCaseStep)

    for forbidden in ("failed_flag", "successful_flag", "result_state"):
        assert forbidden not in columns, forbidden
    assert "expected_outcome" in columns  # what SHOULD happen, not what did


def test_knowledge_case_is_unique_per_source_document():
    """An article reconstructed twice is a duplicate, not a second
    opinion."""
    unique = [
        index
        for index in KnowledgeCase.__table__.indexes
        if index.name == "uq_knowledge_case_source"
    ]
    assert unique and unique[0].unique
    assert {c.name for c in unique[0].columns} == {"tenant_id", "source_evidence_id"}


def test_pattern_evidence_restricts_empirical_to_episodes_in_the_database():
    """The invariant lives in a CHECK constraint because that is the one
    place a future code path cannot forget it. Verified live: inserting a
    knowledge_case row as 'empirical' with an outcome is rejected by
    Postgres, not merely discouraged in a service."""
    checks = [
        c
        for c in PatternEvidence.__table__.constraints
        if c.name == "ck_pattern_evidence_empirical_is_episode"
    ]
    assert checks, "the empirical-is-episode CHECK constraint must exist"

    expression = str(checks[0].sqltext)
    assert "empirical" in expression
    assert "episode" in expression
    assert "outcome IS NULL" in expression


def test_pattern_evidence_records_role_and_class_not_just_membership():
    """PatternEvidenceLink records THAT an episode belongs to a pattern.
    This records what the evidence contributes and on what footing — which
    is what lets a pattern distinguish three KB articles from nineteen
    resolved incidents, instead of one episode_count that cannot."""
    columns = _cols(PatternEvidence)

    for expected in (
        "evidence_object_type",
        "evidence_object_id",
        "support_role",
        "evidence_class",
        "strength",
        "confidence",
        "observed_at",
        "outcome",
    ):
        assert expected in columns, expected


def test_pattern_evidence_can_express_contradiction():
    """A row that contradicts a resolution is evidence too. Losing it is
    how a pattern keeps recommending something that stopped working, and
    it is what makes stale-KB detection possible.

    The role is required and defaults to supporting, so a writer must opt
    IN to recording a contradiction and can never leave the role blank.
    The ORM default covers writes through the model; the migration also
    sets a server default, so a row inserted by raw SQL or a data
    migration is labelled too."""
    column = PatternEvidence.__table__.columns["support_role"]

    assert column.nullable is False
    assert column.default is not None
    assert column.default.arg == "supports_resolution"
