"""Folding answers back into the artifact's provenance.

Pure functions over dicts: no session, no ORM, no LLM. The service calls these
between the model returning a revised playbook and the new version being
written, so the same merge can be unit-tested without a database and without a
generation call.

The important one is ``attest_answers_on_contract``. It is the mechanism that
makes the loop terminate, and it is easy to leave out because nothing visibly
breaks without it — the round applies, the playbook updates, everything looks
fine, and then the next assessment raises the identical
``missing_contract_obligation``, mints the identical ``gap_key``, and asks the
reviewer the identical question. Forever. Writing the answer onto the contract
is what lets ``kb_resolution.resolve_from_context`` recognise, next round, that
a person has already settled this.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from contextedge.quality.clarification.states import (
    ANSWER_BEARING_STATUSES,
    MANDATORY,
    Q_SKIPPED,
)

# How many attested answers the contract snapshot carries forward. Bounded
# because the snapshot is embedded in every content revision and hashed with
# it; an unbounded list would grow the hashed payload with each round and make
# the revision history expensive to read. Newest first, so the bound drops the
# oldest.
MAX_ATTESTED_ANSWERS = 60


def _attr(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def answers_payload(questions: list[Any]) -> list[dict[str, Any]]:
    """Answer-bearing questions, in the shape the revision prompt and the
    provenance blob both want.

    Skipped questions are excluded rather than included as "(skipped)". Telling
    a model that a reviewer declined to answer invites it to treat the silence
    as a statement — usually as permission to fill the gap itself, which is the
    one thing a skip must not authorise.
    """
    out: list[dict[str, Any]] = []
    for question in questions or []:
        if _attr(question, "status") not in ANSWER_BEARING_STATUSES:
            continue
        answer = str(_attr(question, "answer_text") or "").strip()
        if not answer:
            continue
        out.append(
            {
                "gap_key": _attr(question, "gap_key"),
                "gap_kind": _attr(question, "gap_kind"),
                "question": str(_attr(question, "question_text") or "").strip(),
                "answer": answer,
                "source": _attr(question, "answer_source"),
                "obligation": _attr(question, "obligation"),
                "target_kind": _attr(question, "target_kind"),
                "target_ref": _attr(question, "target_ref"),
                "answered_by": (
                    str(_attr(question, "answered_by"))
                    if _attr(question, "answered_by")
                    else None
                ),
                "provenance": _attr(question, "answer_provenance") or None,
            }
        )
    return out


def skipped_payload(questions: list[Any]) -> list[dict[str, Any]]:
    """Optional questions the reviewer declined.

    Recorded — a skip is a decision, and "nobody was asked" and "somebody was
    asked and chose not to say" are different facts about a playbook.
    """
    return [
        {
            "gap_key": _attr(question, "gap_key"),
            "question": str(_attr(question, "question_text") or "").strip(),
        }
        for question in questions or []
        if _attr(question, "status") == Q_SKIPPED
    ]


def attest_answers_on_contract(
    snapshot: dict[str, Any] | None,
    answers: list[dict[str, Any]],
    *,
    round_number: int,
) -> dict[str, Any]:
    """Record answers on the contract snapshot as human attestations.

    Deliberately **not** appended to ``required_actions``. That was the first
    version and it is a trap: the completeness validator would then demand a
    step whose text overlaps the answer, and an answer phrased differently from
    the step it produced becomes a permanent, unsatisfiable obligation — the
    loop's failure mode inverted rather than fixed.

    They go in their own list, which ``resolve_from_context`` reads. The
    obligation keeps being raised by the validator (truthfully — the wording may
    genuinely not match), but the gap resolves without reaching a person again.
    """
    out = dict(snapshot or {})
    existing = [e for e in (out.get("human_attested_answers") or []) if isinstance(e, dict)]
    by_key = {str(e.get("gap_key")): e for e in existing if e.get("gap_key")}

    now = datetime.now(UTC).isoformat()
    for answer in answers:
        key = str(answer.get("gap_key") or "")
        if not key:
            continue
        # A later round's answer supersedes an earlier one for the same gap:
        # the reviewer has revised their position, and keeping both would let
        # the superseded text resolve a future gap.
        by_key[key] = {
            "gap_key": key,
            "gap_kind": answer.get("gap_kind"),
            "question": str(answer.get("question") or "")[:600],
            "answer": str(answer.get("answer") or "")[:2000],
            "source": answer.get("source"),
            "round": round_number,
            "attested_at": now,
            "answered_by": answer.get("answered_by"),
        }

    merged = sorted(
        by_key.values(),
        key=lambda e: (int(e.get("round") or 0), str(e.get("gap_key") or "")),
        reverse=True,
    )
    out["human_attested_answers"] = merged[:MAX_ATTESTED_ANSWERS]
    return out


def merge_clarification_into_evidence_refs(
    existing: dict[str, Any] | None,
    *,
    round_id: Any,
    round_number: int,
    answers: list[dict[str, Any]],
    skipped: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """The new version's ``evidence_refs``, with the round folded in.

    Two writes, and they serve different readers:

    - ``clarification`` is the reviewer's audit trail: which round, what was
      asked, what was answered, by whom, from where. It is what makes a step
      saying something surprising explainable a month later.
    - ``quality_contract.snapshot.human_attested_answers`` is the *validator
      pipeline's* copy, and is what stops the next round re-asking.

    Keeping one and dropping the other is the mistake to avoid: the first alone
    loops forever, the second alone is unauditable.
    """
    refs = dict(existing or {})

    history = [
        entry
        for entry in (refs.get("clarification", {}) or {}).get("rounds", [])
        if isinstance(entry, dict)
    ]
    history.append(
        {
            "round_id": str(round_id) if round_id is not None else None,
            "round_number": round_number,
            "applied_at": datetime.now(UTC).isoformat(),
            "answers": answers,
            "skipped": skipped or [],
            "mandatory_answered": sum(
                1 for a in answers if a.get("obligation") == MANDATORY
            ),
        }
    )
    refs["clarification"] = {"rounds": history[-10:], "latest_round": round_number}

    quality = refs.get("quality_contract")
    if isinstance(quality, dict):
        quality = dict(quality)
        quality["snapshot"] = attest_answers_on_contract(
            quality.get("snapshot") if isinstance(quality.get("snapshot"), dict) else {},
            answers,
            round_number=round_number,
        )
        refs["quality_contract"] = quality
    else:
        # No contract was captured at generation — a legitimate state for an
        # older playbook. Create the minimum shell so the attestations still
        # have somewhere to live and the next round can read them, rather than
        # dropping them because the generation path predates contracts.
        refs["quality_contract"] = {
            "snapshot": attest_answers_on_contract({}, answers, round_number=round_number)
        }

    return refs


def version_data_from_revision(
    revised: dict[str, Any],
    *,
    previous: Any,
    evidence_refs: dict[str, Any],
) -> dict[str, Any]:
    """Build the ``create_playbook_version`` payload from a model revision.

    Every field falls back to the previous version's value. A revision that
    omits a key must not be read as clearing it — a model that returns a
    playbook without ``verification_policy`` has almost certainly forgotten it
    rather than decided the procedure no longer needs verifying, and silently
    dropping policy on a revision is the kind of loss nobody notices until an
    execution goes wrong.
    """

    def _prev(name: str, default: Any = None) -> Any:
        value = getattr(previous, name, None) if previous is not None else None
        return default if value is None else value

    def _pick(name: str, default: Any) -> Any:
        value = revised.get(name)
        if value is None:
            return _prev(name, default)
        return value

    from contextedge.ai.provenance import GENERATION_PROVENANCE_KEY

    payload: dict[str, Any] = {
        "trigger_conditions": _pick("trigger_conditions", {}),
        "branching_logic": _pick("branching_logic", {}),
        "inputs": _pick("inputs", []),
        "outputs": _pick("outputs", []),
        # Steps are the one field with no fallback. A revision that returns no
        # steps is a failed revision, and the caller refuses it rather than
        # writing an empty procedure — see the service.
        "steps": revised.get("steps") or [],
        "rollback_notes": _pick("rollback_notes", None),
        "evidence_refs": evidence_refs,
        "conflicts": _pick("conflicts", None),
        "playbook_confidence": float(
            revised.get("playbook_confidence")
            if revised.get("playbook_confidence") is not None
            else _prev("playbook_confidence", 0.5)
        ),
        "execution_confidence_guidance": _pick("execution_confidence_guidance", None),
        "verification_policy": _pick("verification_policy", None),
    }
    if revised.get(GENERATION_PROVENANCE_KEY):
        payload[GENERATION_PROVENANCE_KEY] = revised[GENERATION_PROVENANCE_KEY]
    return payload
