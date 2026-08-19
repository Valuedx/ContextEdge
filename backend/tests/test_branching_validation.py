"""Structural validation of generated playbook decision points.

Auditing the 190 generated playbooks found 20 with branching defects —
39% of the 51 that branch at all. A decision point whose branches are
identical, or that targets a step which does not exist, reads as
considered control flow and executes as nothing.
"""

from __future__ import annotations

from contextedge.ai.generators.playbook_generator import sanitize_branching_logic


def _result(points, step_count=4):
    return {
        "steps": [{"order": i, "text": f"step {i}"} for i in range(1, step_count + 1)],
        "branching_logic": {"decision_points": points},
    }


def _kept(result):
    return result["branching_logic"]["decision_points"]


def test_branch_that_decides_nothing_is_dropped():
    """Both paths landing on the same step is the defect seen most often
    in the live corpus: it presents as a decision and controls nothing."""
    result = _result([{"after_step": 2, "if_true_goto": 3, "if_false_goto": 3}])
    counts = sanitize_branching_logic(result)

    assert _kept(result) == []
    assert counts == {"kept": 0, "dropped": 1}


def test_target_naming_a_nonexistent_step_is_dropped():
    result = _result([{"after_step": 1, "if_true_goto": 2, "if_false_goto": 99}])

    sanitize_branching_logic(result)

    assert _kept(result) == []


def test_anchor_naming_a_nonexistent_step_is_dropped():
    result = _result([{"after_step": 42, "if_true_goto": 2, "if_false_goto": 3}])

    sanitize_branching_logic(result)

    assert _kept(result) == []


def test_self_loop_is_dropped():
    """Jumping back to the step just finished never terminates."""
    result = _result([{"after_step": 2, "if_true_goto": 2, "if_false_goto": 3}])

    sanitize_branching_logic(result)

    assert _kept(result) == []


def test_real_branch_survives_untouched():
    """The guard must not eat working control flow — the whole point is
    that a playbook with good branching keeps it."""
    point = {"after_step": 1, "if_true_goto": 2, "if_false_goto": 4}
    result = _result([point])

    counts = sanitize_branching_logic(result)

    assert _kept(result) == [point]
    assert counts == {"kept": 1, "dropped": 0}


def test_valid_points_survive_alongside_invalid_ones():
    """One bad point does not discard the playbook's whole control flow."""
    good = {"after_step": 1, "if_true_goto": 2, "if_false_goto": 4}
    dead = {"after_step": 2, "if_true_goto": 3, "if_false_goto": 3}
    result = _result([good, dead])

    counts = sanitize_branching_logic(result)

    assert _kept(result) == [good]
    assert counts == {"kept": 1, "dropped": 1}


def test_missing_or_malformed_branching_is_not_an_error():
    """Most playbooks have no decision points at all; degrade, never raise."""
    assert sanitize_branching_logic({"steps": []}) == {"kept": 0, "dropped": 0}
    assert sanitize_branching_logic(
        {"steps": [], "branching_logic": "nonsense"}
    ) == {"kept": 0, "dropped": 0}
    assert sanitize_branching_logic(
        {"steps": [], "branching_logic": {"decision_points": None}}
    ) == {"kept": 0, "dropped": 0}


def test_a_point_that_strands_a_step_is_dropped():
    """Seen live: step 1 branched to 3 or 4, so nothing ever reached step
    2. Every point was individually well-formed — stranding only exists
    across the whole graph."""
    result = _result([{"after_step": 1, "if_true_goto": 3, "if_false_goto": 4}])

    counts = sanitize_branching_logic(result)

    assert _kept(result) == []
    assert counts == {"kept": 0, "dropped": 1}


def test_switch_shape_survives_because_a_sibling_reaches_the_step():
    """Several points sharing one anchor is a switch — one diagnosis
    routing to several remedies. A step one point jumps over is reached by
    its sibling, so nothing here is stranded and nothing may be dropped."""
    points = [
        {"after_step": 1, "if_true_goto": 2, "if_false_goto": 3},
        {"after_step": 1, "if_true_goto": 3, "if_false_goto": 4},
    ]
    result = _result(points, step_count=4)

    counts = sanitize_branching_logic(result)

    assert _kept(result) == points
    assert counts == {"kept": 2, "dropped": 0}


def test_stranding_repair_keeps_the_branching_that_still_works():
    """Dropping the offending jump restores fall-through; a second,
    innocent decision point must survive it."""
    stranding = {"after_step": 1, "if_true_goto": 3, "if_false_goto": 4}
    innocent = {"after_step": 4, "if_true_goto": 5, "if_false_goto": 6}
    result = _result([stranding, innocent], step_count=6)

    counts = sanitize_branching_logic(result)

    assert _kept(result) == [innocent]
    assert counts == {"kept": 1, "dropped": 1}


def test_forward_jump_over_no_step_is_not_stranding():
    """A jump to the immediately next step skips nothing."""
    point = {"after_step": 1, "if_true_goto": 2, "if_false_goto": 3}
    result = _result([point], step_count=3)

    counts = sanitize_branching_logic(result)

    assert _kept(result) == [point]
    assert counts["dropped"] == 0


def test_non_object_decision_point_is_dropped_not_raised():
    result = _result(["not a dict", {"after_step": 1, "if_true_goto": 2, "if_false_goto": 4}])

    counts = sanitize_branching_logic(result)

    assert counts == {"kept": 1, "dropped": 1}
