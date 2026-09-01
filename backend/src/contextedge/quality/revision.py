"""Building the immutable content snapshot that quality is assessed against.

The reason this module exists is a data-model fact: ``Playbook`` holds the
title and description; ``PlaybookVersion`` holds the steps. Neither row is the
artifact. A quality record stored on the version can be perfectly valid while
the title above it says something else entirely, and an approved playbook's
title can be edited directly (``PATCH /api/v1/playbooks/{id}``) with nothing
downstream noticing.

``build_content`` is therefore the definition of "the playbook" for quality
purposes: both halves, in one dict, hashed as one thing.

The plan offers a compatibility option (§6.3) — move title/description onto
the version, or make the hash span both rows. This takes the second, because
it is additive: no existing writer changes, and the shell keeps its stable
identity and lifecycle metadata where the rest of the system expects them.
"""

from __future__ import annotations

from typing import Any

from contextedge.quality.hashing import content_hash

# Fields of a step that bear on quality. A step carries editor bookkeeping
# (index, edited_at, human_edited) that changes without changing the
# instruction; hashing those would mint a revision for a no-op save.
_STEP_QUALITY_KEYS: tuple[str, ...] = (
    "step_id",
    "order",
    "type",
    "text",
    "title",
    "description",
    "action",
    "instruction",
    "expected_outcome",
    "on_failure",
    "verification",
    "rollback_hint",
    "reason",
    "safety_class",
    "action_type",
    "action_name",
    "tool_ref",
    "requires_approval",
    "reversible",
    "source_refs",
    "grounding_status",
    "step_classification",
    "evidence_quality",
    "confidence",
    "status",
    # Bears on quality, not bookkeeping: a hand-edited step that kept the
    # citations of the sentence it replaced is making a grounding claim about
    # text no source ever said. See validators/grounding.py.
    "human_edited",
)


# Shell (``playbooks`` row) fields that ``build_content`` snapshots.
#
# Exported so the PATCH endpoint can decide whether an edit is quality-bearing
# by intersecting with this set, rather than repeating the field names at the
# call site. The repeated-list version of this shipped first and was already
# wrong: it reassessed on title and description but not on risk_tier or
# automation_mode, both of which are in the content hash. Any list that has to
# be kept in step with this function by hand will drift from it again.
SHELL_QUALITY_FIELDS: frozenset[str] = frozenset(
    {"title", "description", "risk_tier", "automation_mode", "domain_id"}
)


def normalize_step(step: Any) -> dict[str, Any]:
    """One step reduced to the fields that can make it right or wrong."""
    if not isinstance(step, dict):
        return {"text": str(step)}
    out: dict[str, Any] = {}
    for key in _STEP_QUALITY_KEYS:
        if key in step:
            out[key] = step[key]
    return out


def build_content(playbook: Any, version: Any | None) -> dict[str, Any]:
    """The quality-bearing content of a playbook, across both rows.

    ``version`` may be ``None`` — a shell with no version yet is a legitimate
    thing to snapshot, and produces a revision whose structural validator will
    fail it for having no procedure. That is the correct outcome and is very
    much better than declining to assess it.
    """
    steps = []
    trigger_conditions: Any = {}
    branching_logic: Any = {}
    inputs: Any = []
    outputs: Any = []
    rollback_notes: Any = None
    evidence_refs: Any = None
    conflicts: Any = None
    semantic_version: Any = None
    generation_provenance: Any = None
    playbook_confidence: Any = None
    execution_confidence_guidance: Any = None
    verification_policy: Any = None

    if version is not None:
        steps = [normalize_step(step) for step in (getattr(version, "steps", None) or [])]
        trigger_conditions = getattr(version, "trigger_conditions", None) or {}
        branching_logic = getattr(version, "branching_logic", None) or {}
        inputs = getattr(version, "inputs", None) or []
        outputs = getattr(version, "outputs", None) or []
        rollback_notes = getattr(version, "rollback_notes", None)
        evidence_refs = getattr(version, "evidence_refs", None)
        conflicts = getattr(version, "conflicts", None)
        semantic_version = getattr(version, "semantic_version", None)
        generation_provenance = getattr(version, "generation_provenance", None)
        playbook_confidence = getattr(version, "playbook_confidence", None)
        execution_confidence_guidance = getattr(version, "execution_confidence_guidance", None)
        verification_policy = getattr(version, "verification_policy", None)

    return {
        # --- shell half
        "title": getattr(playbook, "title", None),
        "description": getattr(playbook, "description", None),
        "risk_tier": getattr(playbook, "risk_tier", None),
        "automation_mode": getattr(playbook, "automation_mode", None),
        "domain_id": getattr(playbook, "domain_id", None),
        # --- version half
        "semantic_version": semantic_version,
        "trigger_conditions": trigger_conditions,
        "branching_logic": branching_logic,
        "inputs": inputs,
        "outputs": outputs,
        "steps": steps,
        "rollback_notes": rollback_notes,
        "evidence_refs": evidence_refs,
        "conflicts": conflicts,
        "generation_provenance": generation_provenance,
        # Three fields an operator or reviewer acts on, so a change to any of
        # them is a change to the artifact being judged.
        #
        # `verification_policy` and `execution_confidence_guidance` are plainly
        # content — one is policy, the other is text shown to the engineer
        # running the procedure. `playbook_confidence` is more arguable: it is
        # a number the model emitted about its own output rather than an
        # instruction. It is included because it is surfaced to reviewers as
        # the Score column and weighed in their decision, and because in
        # practice it only changes when a version is created, so including it
        # costs no spurious revisions.
        "playbook_confidence": playbook_confidence,
        "execution_confidence_guidance": execution_confidence_guidance,
        "verification_policy": verification_policy,
    }


def compute_content_hash(playbook: Any, version: Any | None) -> str:
    return content_hash(build_content(playbook, version))


def summarize_change(previous: dict | None, current: dict) -> list[str]:
    """Which quality-bearing fields differ. Used for the staleness reason and
    for the reviewer's "what changed" line.

    Returns field names, not a diff — a diff belongs in the UI, and a
    truncated one stored on an audit row is worse than a list of names.
    """
    if previous is None:
        return ["*"]
    changed: list[str] = []
    for key in sorted(set(previous) | set(current)):
        if previous.get(key) != current.get(key):
            changed.append(key)
    return changed
