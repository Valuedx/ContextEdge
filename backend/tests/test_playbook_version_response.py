"""The response model must accept what the database legitimately holds.

`GET /playbooks/{id}/versions` returned 500 for every playbook. Nothing
was wrong with the data or the query: FastAPI validated the endpoint's
own OUTPUT against `PlaybookVersionResponse`, found `evidence_refs`
declared as `list` while the generator writes an object, and raised
ResponseValidationError.

The failure was invisible from every angle except the browser. The rows
were correct, the service layer was correct, and the whole test suite was
green — because nothing ever built the response model from a real stored
row. On the page, the version query failed, `versions` stayed empty, and
the section rendered "No published versions yet", so a playbook with six
steps read as a playbook with none.

These tests build the response model from both shapes the column holds.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from contextedge.schemas.playbook import PlaybookVersionResponse

# The shape the generator writes: episode evidence and the knowledge that
# grounds the playbook normatively are different things, and a flat list
# cannot say which is which.
OBJECT_REFS = {
    "pattern_id": str(uuid4()),
    "episode_ids": [str(uuid4())],
    "evidence_ids": [str(uuid4()), str(uuid4())],
    "knowledge_ids": [str(uuid4())],
    "knowledge": [
        {
            "title": "ae license details are not visible on ae server",
            "evidence_id": str(uuid4()),
            "evidence_type": "kb_article",
            "applicability_notes": [],
            "applicability_verdict": "unknown",
        }
    ],
}


def _payload(**over):
    base = {
        "id": uuid4(),
        "playbook_id": uuid4(),
        "semantic_version": "0.1.0",
        "trigger_conditions": {},
        "branching_logic": {},
        "inputs": [],
        "outputs": [],
        "steps": [{"order": 1, "text": "Restart the broker.", "type": "remediation"}],
        "rollback_notes": None,
        "evidence_refs": OBJECT_REFS,
        "playbook_confidence": 0.8,
        "execution_confidence_guidance": None,
        "published_at": None,
        "published_by": None,
        "created_at": datetime.now(UTC),
    }
    base.update(over)
    return base


def test_object_evidence_refs_are_accepted():
    """The regression. 33 of 37 stored versions hold this shape, and
    declaring only `list` turned every one of them into a 500."""
    response = PlaybookVersionResponse(**_payload())
    assert response.evidence_refs["pattern_id"] == OBJECT_REFS["pattern_id"]
    assert response.evidence_refs["knowledge"][0]["evidence_type"] == "kb_article"


def test_legacy_list_evidence_refs_are_still_accepted():
    """Four older rows hold a bare list. Narrowing the model to objects
    would swap one 500 for another rather than fixing anything."""
    refs = [str(uuid4()), str(uuid4())]
    response = PlaybookVersionResponse(**_payload(evidence_refs=refs))
    assert response.evidence_refs == refs


def test_absent_evidence_refs_are_accepted():
    assert PlaybookVersionResponse(**_payload(evidence_refs=None)).evidence_refs is None


def test_steps_survive_the_response_model():
    """What the page is actually there to show. The steps were never the
    problem — they simply never reached the browser, because the response
    they travelled in was rejected whole."""
    response = PlaybookVersionResponse(**_payload())
    assert len(response.steps) == 1
    assert response.steps[0]["text"] == "Restart the broker."


def test_conflicts_are_exposed():
    """The column exists on the model and the frontend renders a panel
    from it, but the response schema omitted it — so the panel could
    never populate."""
    conflicts = [{"topic": "restart order", "documented": "A", "observed": "B"}]
    response = PlaybookVersionResponse(**_payload(conflicts=conflicts))
    assert response.conflicts == conflicts
    # Absent is not the same as empty: versions predating the field carry
    # null, which means "not assessed" rather than "no conflicts".
    assert PlaybookVersionResponse(**_payload()).conflicts is None


def test_a_real_row_shape_round_trips_from_attributes():
    """Built the way FastAPI builds it — from the ORM object, not a dict.
    `from_attributes` is what the endpoint relies on, and a model that
    validates dicts but not rows would still 500 in production."""
    from types import SimpleNamespace

    row = SimpleNamespace(**_payload(), verification_policy=None)
    response = PlaybookVersionResponse.model_validate(row, from_attributes=True)
    assert response.evidence_refs["episode_ids"]
    assert response.steps
    assert response.is_editable is True
    assert response.revision == 1


def test_editing_fields_default_on_legacy_payloads():
    response = PlaybookVersionResponse(**_payload())
    assert response.revision == 1
    assert response.derived_from_version_id is None
    assert response.edit_warnings == []
    assert response.last_edit_note is None


def test_last_edit_note_is_optional_on_the_response():
    response = PlaybookVersionResponse(**_payload(last_edit_note="Clarify restart order"))
    assert response.last_edit_note == "Clarify restart order"


def test_a_genuinely_wrong_type_is_still_rejected():
    """Widening the field must not turn it into `Any` — a string where a
    structure belongs is still a bug worth failing on."""
    with pytest.raises(ValidationError):
        PlaybookVersionResponse(**_payload(evidence_refs="not-a-structure"))
