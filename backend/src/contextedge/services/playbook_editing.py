"""In-place editing of unpublished playbook versions.

Published versions are immutable (``trg_playbook_versions_steps_immutable``).
This module is the only writer of draft ``steps`` JSONB: it merges patches
onto stored dicts so provenance keys (``source_refs``, ``grounding_status``,
vendor fields) cannot be stripped by a typed round-trip.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from contextedge.models.execution import ACTION_TYPES, SAFETY_CLASSES
from contextedge.schemas.playbook import PlaybookStepPatch

MAX_STEPS = 100
MAX_STEPS_BYTES = 512 * 1024
MAX_INSTRUCTION_CHARS = 4_000
MAX_TRIGGER_BYTES = 32 * 1024
MAX_ROLLBACK_CHARS = 8 * 1024

KNOWN_STEP_TYPES = frozenset(
    {"diagnostic", "remediation", "verification", "escalation", "communication"}
)

INSTRUCTION_KEYS = ("text", "title", "description", "action", "instruction")

EDITABLE_KEYS = frozenset(
    {
        "text",
        "title",
        "description",
        "type",
        "expected_outcome",
        "on_failure",
        "reason",
        "rollback_hint",
        "safety_class",
        "action_type",
        "action_name",
        "tool_ref",
        "requires_approval",
        "reversible",
        "verification",
        "time_estimate_sec",
    }
)

# Never copied from a client patch onto a stored step. The merge keeps
# whatever the stored object already had.
PROTECTED_KEYS = frozenset(
    {
        "step_id",
        "order",
        "index",
        "source_refs",
        "grounding_status",
        "evidence_quality",
        "step_classification",
        "human_edited",
        "edited_by",
        "edited_at",
        "created_by",
        "created_at",
    }
)


class PlaybookEditValidationError(Exception):
    """Raised for 422-class step / payload validation failures."""


def step_instruction(step: dict[str, Any]) -> str:
    for key in INSTRUCTION_KEYS:
        value = step.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _as_step_dict(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    return {}


def _patch_values(patch: PlaybookStepPatch) -> dict[str, Any]:
    dumped = patch.model_dump(exclude_unset=True, exclude={"step_id", "clear_fields"})
    return {key: value for key, value in dumped.items() if key in EDITABLE_KEYS}


def _apply_clear(merged: dict[str, Any], clear_fields: list[str]) -> None:
    for key in clear_fields:
        if key in PROTECTED_KEYS or key == "step_id":
            continue
        merged.pop(key, None)


def _instruction_changed(stored: dict[str, Any], merged: dict[str, Any]) -> bool:
    return step_instruction(stored) != step_instruction(merged)


def normalize_steps(
    existing: list | None,
    patches: list[PlaybookStepPatch],
    actor_id: uuid.UUID,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Merge an ordered patch list onto stored steps.

    Existing steps without ``step_id`` are matched positionally to patches
    that do not resolve by id (first save after the editor assigned temp
    ids). Omitted stored steps are deleted. ``order`` / ``index`` are
    rewritten to ``1..n`` / ``0..n-1`` so they cannot disagree with array
    position.
    """
    now = datetime.now(UTC).isoformat()
    existing_steps = [_as_step_dict(item) for item in (existing or [])]

    by_id: dict[str, dict[str, Any]] = {}
    no_id: list[dict[str, Any]] = []
    for step in existing_steps:
        sid = step.get("step_id")
        if isinstance(sid, str) and sid.strip():
            if sid in by_id:
                raise PlaybookEditValidationError(f"duplicate stored step_id {sid}")
            by_id[sid] = step
        else:
            no_id.append(step)

    original_order = [
        s.get("step_id") if isinstance(s.get("step_id"), str) else None
        for s in existing_steps
    ]

    seen_patch_ids: set[str] = set()
    used_ids: set[str] = set()
    result: list[dict[str, Any]] = []
    added: list[str] = []
    modified: list[str] = []

    for patch in patches:
        pid = patch.step_id.strip() if isinstance(patch.step_id, str) and patch.step_id.strip() else None
        if pid is not None:
            if pid in seen_patch_ids:
                raise PlaybookEditValidationError(f"duplicate step_id {pid}")
            seen_patch_ids.add(pid)

        stored: dict[str, Any] | None = None
        if pid is not None and pid in by_id and pid not in used_ids:
            stored = by_id[pid]
        elif no_id:
            stored = no_id.pop(0)

        values = _patch_values(patch)

        if stored is None:
            sid = pid or str(uuid.uuid4())
            if sid in used_ids:
                raise PlaybookEditValidationError(f"duplicate step_id {sid}")
            new_step: dict[str, Any] = {
                "step_id": sid,
                "grounding_status": "non_grounded",
                "step_classification": "human_authored",
                "source_refs": [],
                "created_by": str(actor_id),
                "created_at": now,
            }
            new_step.update(values)
            _apply_clear(new_step, patch.clear_fields)
            used_ids.add(sid)
            added.append(sid)
            result.append(new_step)
            continue

        merged = dict(stored)
        if pid is not None:
            merged["step_id"] = pid
        elif not isinstance(merged.get("step_id"), str) or not merged["step_id"].strip():
            merged["step_id"] = str(uuid.uuid4())
        sid = str(merged["step_id"])
        if sid in used_ids:
            raise PlaybookEditValidationError(f"duplicate step_id {sid}")
        used_ids.add(sid)

        before = dict(merged)
        merged.update(values)
        _apply_clear(merged, patch.clear_fields)

        if (
            _instruction_changed(before, merged)
            and before.get("grounding_status") == "grounded"
        ):
            merged["human_edited"] = True
            merged["edited_by"] = str(actor_id)
            merged["edited_at"] = now

        if merged != before:
            modified.append(sid)
        result.append(merged)

    removed: list[str] = []
    for sid, leftover in by_id.items():
        if sid not in used_ids:
            removed.append(sid)
    for leftover in no_id:
        leftover_id = leftover.get("step_id")
        removed.append(str(leftover_id) if leftover_id else "(unidentified)")

    for index, step in enumerate(result):
        step["order"] = index + 1
        step["index"] = index

    new_order = [s.get("step_id") for s in result]
    reordered = new_order != [sid for sid in original_order if sid in used_ids] and (
        bool(original_order) or bool(new_order)
    )
    # Reorder is true when relative identity order of surviving steps changed,
    # or when the surviving set was permuted. Compare surviving original ids
    # in original sequence against the new sequence.
    surviving_original = [sid for sid in original_order if sid in used_ids]
    surviving_new = [sid for sid in new_order if sid in (set(surviving_original))]
    reordered = surviving_original != surviving_new

    summary = {
        "added": added,
        "removed": removed,
        "modified": modified,
        "reordered": reordered,
    }
    return result, summary


def validate_steps(steps: list[dict[str, Any]]) -> dict[str, Any]:
    """Hard validation. Empty drafts are allowed (warning only)."""
    warnings: list[str] = []
    if len(steps) == 0:
        warnings.append("draft has no steps; it cannot be sent for review until steps are added")
        return {"warnings": warnings}

    if len(steps) > MAX_STEPS:
        raise PlaybookEditValidationError(
            f"a version may have at most {MAX_STEPS} steps, got {len(steps)}"
        )

    serialized = json.dumps(steps, default=str)
    if len(serialized.encode("utf-8")) > MAX_STEPS_BYTES:
        raise PlaybookEditValidationError(
            f"steps payload exceeds {MAX_STEPS_BYTES} bytes"
        )

    seen: set[str] = set()
    for index, step in enumerate(steps):
        sid = step.get("step_id")
        if not isinstance(sid, str) or not sid.strip():
            raise PlaybookEditValidationError(f"step {index + 1} is missing step_id")
        if sid in seen:
            raise PlaybookEditValidationError(f"duplicate step_id {sid}")
        seen.add(sid)

        instruction = step_instruction(step)
        if not instruction:
            raise PlaybookEditValidationError(
                f"step {index + 1} needs instruction text (text, title, or description)"
            )
        if len(instruction) > MAX_INSTRUCTION_CHARS:
            raise PlaybookEditValidationError(
                f"step {index + 1} instruction exceeds {MAX_INSTRUCTION_CHARS} characters"
            )

        step_type = step.get("type")
        if isinstance(step_type, str) and step_type and step_type not in KNOWN_STEP_TYPES:
            warnings.append(
                f"step {index + 1} has unrecognized type {step_type!r}; stored as-is"
            )

        safety = step.get("safety_class")
        if safety is not None and safety not in SAFETY_CLASSES:
            raise PlaybookEditValidationError(
                f"step {index + 1} safety_class must be one of {SAFETY_CLASSES}"
            )

        action_type = step.get("action_type")
        if action_type is not None and action_type not in ACTION_TYPES:
            raise PlaybookEditValidationError(
                f"step {index + 1} action_type must be one of {ACTION_TYPES}"
            )

        estimate = step.get("time_estimate_sec")
        if estimate is not None:
            if not isinstance(estimate, int) or isinstance(estimate, bool):
                raise PlaybookEditValidationError(
                    f"step {index + 1} time_estimate_sec must be an integer"
                )
            if estimate < 0 or estimate > 86_400:
                raise PlaybookEditValidationError(
                    f"step {index + 1} time_estimate_sec must be between 0 and 86400"
                )

    return {"warnings": warnings}


def validate_version_fields(
    *,
    trigger_conditions: dict | None = None,
    rollback_notes: str | None = None,
) -> None:
    if trigger_conditions is not None:
        encoded = json.dumps(trigger_conditions, default=str)
        if len(encoded.encode("utf-8")) > MAX_TRIGGER_BYTES:
            raise PlaybookEditValidationError(
                f"trigger_conditions exceed {MAX_TRIGGER_BYTES} bytes"
            )
    if rollback_notes is not None and len(rollback_notes) > MAX_ROLLBACK_CHARS:
        raise PlaybookEditValidationError(
            f"rollback_notes exceed {MAX_ROLLBACK_CHARS} characters"
        )
