"""Tests for the golden-eval regression runner (W10-12.3)."""

import json
import pathlib
from unittest.mock import patch

import pytest


@pytest.fixture
def tmp_golden(tmp_path):
    """Write a small golden file and return its path."""
    cases = [
        {
            "id": "c1",
            "title": "VPN outage",
            "body": "cert expired",
            "source_type": "ticket",
            "evidence_type": "ticket",
            "expected_classification": "operational",
            "min_confidence": 0.7,
        },
        {
            "id": "c2",
            "title": "newsletter",
            "body": "monthly digest",
            "source_type": "email",
            "evidence_type": "email",
            "expected_classification": "not_relevant",
            "min_confidence": 0.6,
        },
    ]
    path = tmp_path / "golden.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        for case in cases:
            fh.write(json.dumps(case) + "\n")
    return path


async def _fake_classify_all_correct(title, body, source_type, evidence_type, **kwargs):
    """Perfect classifier for the baseline golden set."""
    if "outage" in (title or "").lower():
        return {"classification": "operational", "confidence": 0.92, "reasoning": ""}
    return {"classification": "not_relevant", "confidence": 0.88, "reasoning": ""}


async def _fake_classify_all_wrong(title, body, source_type, evidence_type, **kwargs):
    """Classifier that gets every label wrong."""
    return {"classification": "possibly_relevant", "confidence": 0.5, "reasoning": ""}


async def _fake_classify_low_confidence(title, body, source_type, evidence_type, **kwargs):
    """Labels right, but confidence below any reasonable threshold."""
    if "outage" in (title or "").lower():
        return {"classification": "operational", "confidence": 0.3, "reasoning": ""}
    return {"classification": "not_relevant", "confidence": 0.3, "reasoning": ""}


def test_all_pass_exits_zero(tmp_golden, capsys):
    from evals.run_regression import main
    import contextedge.ai.classifiers.relevance as relevance_mod

    with patch.object(relevance_mod, "classify_relevance", _fake_classify_all_correct):
        code = main(["relevance", "--golden", str(tmp_golden)])

    out = capsys.readouterr().out
    assert code == 0
    assert "2/2 passed" in out
    assert "PASS" in out


def test_label_mismatch_exits_nonzero(tmp_golden, capsys):
    from evals.run_regression import main
    import contextedge.ai.classifiers.relevance as relevance_mod

    with patch.object(relevance_mod, "classify_relevance", _fake_classify_all_wrong):
        code = main(["relevance", "--golden", str(tmp_golden)])

    out = capsys.readouterr().out
    assert code == 1
    assert "FAIL" in out
    assert "label:" in out


def test_confidence_floor_enforced_even_when_label_right(tmp_golden, capsys):
    """Classifier gets the right label but below the min_confidence
    threshold: the runner must still report FAIL and exit non-zero."""
    from evals.run_regression import main
    import contextedge.ai.classifiers.relevance as relevance_mod

    with patch.object(relevance_mod, "classify_relevance", _fake_classify_low_confidence):
        code = main(["relevance", "--golden", str(tmp_golden)])

    out = capsys.readouterr().out
    assert code == 1
    assert "confidence:" in out


def test_missing_golden_file_exits_two(tmp_path, capsys):
    from evals.run_regression import main

    code = main(["relevance", "--golden", str(tmp_path / "nope.jsonl")])
    err = capsys.readouterr().err
    assert code == 2
    assert "golden file not found" in err


def test_empty_golden_file_exits_two(tmp_path, capsys):
    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    from evals.run_regression import main

    code = main(["relevance", "--golden", str(empty)])
    err = capsys.readouterr().err
    assert code == 2
    assert "no cases" in err


def test_confusion_matrix_is_printed(tmp_golden, capsys):
    from evals.run_regression import main
    import contextedge.ai.classifiers.relevance as relevance_mod

    with patch.object(relevance_mod, "classify_relevance", _fake_classify_all_correct):
        main(["relevance", "--golden", str(tmp_golden)])

    out = capsys.readouterr().out
    assert "Confusion matrix" in out
    assert "operational" in out
    assert "not_relevant" in out


def test_real_golden_file_exists_and_parses():
    """Regression: the committed golden.jsonl must stay valid — a
    half-written case kills CI once we wire this into a weekly run."""
    golden = pathlib.Path(__file__).parents[1] / "evals" / "relevance" / "golden.jsonl"
    assert golden.exists()
    cases = []
    with golden.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            case = json.loads(line)
            assert "id" in case
            assert "expected_classification" in case
            cases.append(case)
    assert len(cases) >= 3, "golden set should have at least a handful of cases"


def test_runner_requires_known_kind():
    from evals.run_regression import main

    with pytest.raises(SystemExit):  # argparse exits with SystemExit(2)
        main(["nonexistent-extractor"])
