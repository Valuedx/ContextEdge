"""CLI: run a golden eval set through the live extractor and report.

Example:

.. code-block:: bash

    cd backend
    python -m evals.run_regression relevance

Prints per-case pass/fail, the confusion matrix, and aggregate stats
(accuracy, pass count, cost estimate). Exit code is 0 on all-pass, 1
on any failure — wire this into CI when the customer signs off on a
pass bar.

The runner keeps itself small on purpose. It is **not** a benchmark
framework — it's a smoke test. When the golden set grows past ~50
cases per extractor, switch to a dedicated eval runner (DeepEval /
promptfoo / in-house) rather than scaling this script up.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import sys
from dataclasses import dataclass
from typing import Callable, Awaitable


@dataclass
class _CaseResult:
    id: str
    expected: str
    predicted: str
    confidence: float
    label_match: bool
    confidence_ok: bool

    @property
    def passed(self) -> bool:
        return self.label_match and self.confidence_ok


def _load_cases(path: pathlib.Path) -> list[dict]:
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


async def _run_relevance(cases: list[dict]) -> list[_CaseResult]:
    # Local import so ``python -m evals.run_regression --help`` works
    # without the full backend importable (helps in CI bootstrap).
    from contextedge.ai.classifiers.relevance import classify_relevance

    results: list[_CaseResult] = []
    for case in cases:
        expected = case["expected_classification"]
        min_conf = float(case.get("min_confidence") or 0.0)
        out = await classify_relevance(
            title=case.get("title", ""),
            body=case.get("body", ""),
            source_type=case.get("source_type", "unknown"),
            evidence_type=case.get("evidence_type", "unknown"),
        )
        predicted = out.get("classification", "")
        confidence = float(out.get("confidence", 0.0))
        results.append(
            _CaseResult(
                id=case["id"],
                expected=expected,
                predicted=predicted,
                confidence=confidence,
                label_match=(predicted == expected),
                confidence_ok=(confidence >= min_conf),
            )
        )
    return results


_RUNNERS: dict[str, Callable[[list[dict]], Awaitable[list[_CaseResult]]]] = {
    "relevance": _run_relevance,
}


def _print_report(kind: str, results: list[_CaseResult]) -> int:
    passed = [r for r in results if r.passed]
    failed = [r for r in results if not r.passed]
    print(f"\n=== {kind} regression ({len(results)} cases) ===\n")

    for r in results:
        tag = "PASS" if r.passed else "FAIL"
        detail = ""
        if not r.label_match:
            detail += f" label: expected={r.expected} got={r.predicted}"
        if not r.confidence_ok:
            detail += f" confidence: {r.confidence:.2f} (below threshold)"
        print(f"  [{tag}] {r.id}{detail}")

    # Confusion matrix.
    labels = sorted({r.expected for r in results} | {r.predicted for r in results})
    if labels:
        print("\nConfusion matrix (rows=expected, cols=predicted):")
        header = "    " + " ".join(f"{lbl[:10]:>10}" for lbl in labels)
        print(header)
        for exp in labels:
            cells = []
            for pred in labels:
                cells.append(
                    sum(1 for r in results if r.expected == exp and r.predicted == pred)
                )
            print(f"    " + " ".join(f"{c:>10}" for c in cells) + f"  {exp}")

    accuracy = (len(passed) / len(results)) if results else 0.0
    print(f"\nSummary: {len(passed)}/{len(results)} passed  accuracy={accuracy:.1%}\n")
    return 0 if not failed else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a golden regression set.")
    parser.add_argument("kind", choices=sorted(_RUNNERS.keys()))
    parser.add_argument(
        "--golden",
        default=None,
        help="Override the default golden.jsonl path (for ad-hoc testing).",
    )
    args = parser.parse_args(argv)

    runner = _RUNNERS[args.kind]
    golden_path = pathlib.Path(args.golden) if args.golden else (
        pathlib.Path(__file__).parent / args.kind / "golden.jsonl"
    )
    if not golden_path.exists():
        print(f"ERROR: golden file not found: {golden_path}", file=sys.stderr)
        return 2

    cases = _load_cases(golden_path)
    if not cases:
        print(f"ERROR: no cases in {golden_path}", file=sys.stderr)
        return 2

    results = asyncio.run(runner(cases))
    return _print_report(args.kind, results)


if __name__ == "__main__":
    sys.exit(main())
