"""The harness that decides whether a prompt or model change is better.

Without it, the only easy number was how many entities came back — and a
prompt that removes junk by removing entities is indistinguishable from
one that removes junk, in that number. v3 sat opt-in for exactly that
reason until these metrics separated the two.

Only the pure scoring is tested here. The runner needs a provider.
"""

from __future__ import annotations

import pytest

from contextedge.evals.extraction_eval import (
    Case,
    is_spec_violation,
    load_cases,
    names_from_payload,
    score_case,
    stability,
)


# --- violations need no labels ------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "spoolsv.exe",
        "HPZ5r5064.DLL",
        "services.msc",
        "0x800CCC0F",
        "%ASA-4-113029",
        "application/json",
        "INC0020902",
        "CHG0044131",
        "61616",
        r"C:\Windows\System32",
        "",
    ],
)
def test_shapes_that_are_never_an_entity(name):
    """These are wrong in any corpus for any tenant, so they can be
    scored on documents nobody has labelled."""
    assert is_spec_violation(name)


@pytest.mark.parametrize(
    "name",
    [
        "ActiveMQ",
        "Print Spooler",
        "HP Universal Print Driver",
        "VPNGW01",
        "MAILGW01",
        "J. Smith",
        "London DC",
        "edge-gw-01",
    ],
)
def test_real_entities_are_not_flagged(name):
    assert not is_spec_violation(name)


def test_the_violation_rules_are_not_a_restatement_of_the_prompt():
    """A metric that repeats the prompt's own wording only proves the
    model read it back. These are shape rules, derived independently."""
    import inspect

    from contextedge.ai.prompts import identity
    from contextedge.evals import extraction_eval

    metric_source = inspect.getsource(extraction_eval)
    assert "_VIOLATION_PATTERNS" in metric_source
    # The dataset and the metrics must not import the prompt text.
    assert "_V3_SYSTEM" not in metric_source
    assert identity._V3_SYSTEM not in metric_source


# --- gold labels --------------------------------------------------------------


def test_a_missing_required_entity_is_counted():
    case = Case(case_id="c", text="t", must_include=["ActiveMQ", "AutomationEdge"])
    score = score_case(case, ["ActiveMQ"])
    assert score.missing == ["AutomationEdge"]


def test_matching_is_substring_in_both_directions():
    """"AutomationEdge agent" satisfies a label of "AutomationEdge", and
    a label of "spoolsv.exe" is violated by "spoolsv.exe (spooler)".
    Exact matching would score phrasing, not correctness."""
    case = Case(
        case_id="c",
        text="t",
        must_include=["AutomationEdge"],
        must_exclude=["spoolsv.exe"],
    )
    score = score_case(case, ["AutomationEdge agent", "spoolsv.exe (print spooler)"])
    assert score.missing == []
    assert score.forbidden == ["spoolsv.exe (print spooler)"]


def test_case_is_ignored_when_matching():
    case = Case(case_id="c", text="t", must_include=["activemq"])
    assert score_case(case, ["ActiveMQ"]).missing == []


def test_an_empty_answer_can_be_the_correct_answer():
    """Six of the dataset's cases name nothing at all. A prompt that
    returns entities for them is wrong, and a harness that could not
    express that would reward over-extraction."""
    case = Case(case_id="c", text="t", must_exclude=["laptop", "email"])
    assert score_case(case, []).forbidden == []
    assert score_case(case, ["laptop"]).forbidden == ["laptop"]


# --- stability ----------------------------------------------------------------


def test_identical_runs_score_perfectly_stable():
    samples = [["ActiveMQ", "MAILGW01"]] * 3
    result = stability(samples)
    assert result["jaccard"] == 1.0
    assert result["count_stdev"] == 0.0


def test_a_run_that_drops_an_entity_is_visible():
    """The measurement that was missing. Counting alone cannot tell a
    prompt that lost an entity from one that never found it."""
    samples = [["ActiveMQ", "MAILGW01"], ["ActiveMQ"], ["ActiveMQ", "MAILGW01"]]
    result = stability(samples)
    assert result["jaccard"] < 1.0
    assert result["count_stdev"] > 0.0


def test_a_single_sample_cannot_measure_stability():
    """It reports perfect rather than pretending to know — which is why
    the six-document, one-sample comparison was unable to settle v3."""
    assert stability([["ActiveMQ"]])["jaccard"] == 1.0


def test_ordering_does_not_affect_stability():
    samples = [["a", "b", "c"], ["c", "a", "b"]]
    assert stability(samples)["jaccard"] == 1.0


# --- payload shapes -----------------------------------------------------------


def test_both_prompt_output_shapes_are_read():
    """v1 and v2 emit `name`; v3 emits `display_name`. The harness has to
    read both or it cannot compare them."""
    assert names_from_payload({"entities": [{"name": "ActiveMQ"}]}) == ["ActiveMQ"]
    assert names_from_payload({"entities": [{"display_name": "ActiveMQ"}]}) == [
        "ActiveMQ"
    ]


def test_a_malformed_payload_yields_nothing_rather_than_raising():
    for payload in [None, [], {"entities": "nope"}, {}, {"entities": [None, 3]}]:
        assert names_from_payload(payload) == []


# --- the dataset itself -------------------------------------------------------


def test_the_shipped_dataset_loads_and_is_labelled():
    cases = load_cases()
    assert len(cases) >= 15
    assert all(case.case_id and case.text for case in cases)
    assert all(case.is_labelled for case in cases)


def test_case_ids_are_unique():
    """Duplicated ids would silently overwrite each other in any
    per-case report."""
    ids = [case.case_id for case in load_cases()]
    assert len(ids) == len(set(ids))


def test_the_dataset_covers_empty_answer_cases():
    """A set where every case expects entities cannot detect
    over-extraction, which is the failure mode that filled this graph
    with ticket numbers."""
    cases = load_cases()
    assert sum(1 for case in cases if not case.must_include) >= 4
