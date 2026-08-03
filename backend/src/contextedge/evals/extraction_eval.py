"""Score entity extraction: precision, recall, and stability.

Three metric families, because no one of them alone decides anything.

**Spec violations** need no labels at all. A name ending ``.exe``, an
error code, a MIME type — these are wrong in any corpus, for any tenant,
so they can be counted on documents nobody has labelled. This is the
metric that showed v3 removing junk 4 -> 0.

**Gold precision and recall** need labels, and are the only way to see
what an exclusion rule costs. A prompt that returns nothing scores
perfectly on violations.

**Stability** needs no labels either, and is the one that was missing.
Running each case several times and measuring how much the answer moves
is what separates "v3 dropped a real entity" from "v3 is noisy" — a
distinction six single-sample documents could not make, and the reason
v3 is still not the default.
"""

from __future__ import annotations

import json
import re
import statistics
from dataclasses import dataclass, field
from pathlib import Path

DATASET_DIR = Path(__file__).parent / "datasets"

# Shapes that are never an entity, in any corpus. Kept separate from the
# prompt's own wording on purpose: a metric that restates the prompt only
# proves the model read it back.
_VIOLATION_SUFFIXES = (".exe", ".dll", ".msc", ".log", ".conf", ".bat", ".sh", ".jar")
_VIOLATION_PATTERNS = (
    re.compile(r"^0x[0-9a-f]+$", re.IGNORECASE),        # hex status codes
    re.compile(r"^%[a-z]+-\d+-\d+", re.IGNORECASE),      # vendor log identifiers
    re.compile(r"^[a-z]+/[a-z]+$", re.IGNORECASE),       # MIME-ish types
    re.compile(r"^(inc|chg|req|tkt)\d{4,}$", re.IGNORECASE),  # ticket records
    re.compile(r"^\d+$"),                                 # bare numbers, incl. ports
    re.compile(r"^[a-z]:\\", re.IGNORECASE),              # filesystem paths
)


def normalize(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip().lower())


def is_spec_violation(name: str) -> bool:
    """True when a name is wrong regardless of context or tenant."""
    lowered = normalize(name)
    if not lowered:
        return True
    if lowered.endswith(_VIOLATION_SUFFIXES):
        return True
    return any(pattern.match(lowered) for pattern in _VIOLATION_PATTERNS)


@dataclass(slots=True)
class Case:
    """One document and what a correct extraction must and must not say.

    Both label lists are partial on purpose. Enumerating every entity in
    a 10k-character article is not sustainable, and a set nobody
    maintains is worse than a small one that is trusted — so `recall`
    here means "of the things we insisted on", not "of everything
    present".
    """

    case_id: str
    text: str
    must_include: list[str] = field(default_factory=list)
    must_exclude: list[str] = field(default_factory=list)
    note: str | None = None

    @property
    def is_labelled(self) -> bool:
        return bool(self.must_include or self.must_exclude)


@dataclass(slots=True)
class CaseScore:
    case_id: str
    extracted: int
    violations: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    forbidden: list[str] = field(default_factory=list)


def score_case(case: Case, extracted_names: list[str]) -> CaseScore:
    """Compare one extraction against one case's labels.

    Matching is substring-based in both directions: a prompt returning
    "AutomationEdge agent" satisfies a label of "AutomationEdge", and a
    label of "spoolsv.exe" is violated by "spoolsv.exe (print spooler)".
    Exact matching would fail extractions that are right but worded
    differently, which would make the score measure phrasing.
    """
    found = [normalize(n) for n in extracted_names if normalize(n)]

    missing = [
        want
        for want in case.must_include
        if not any(normalize(want) in name or name in normalize(want) for name in found)
    ]
    forbidden = [
        name
        for name in found
        if any(normalize(bad) in name for bad in case.must_exclude)
    ]
    violations = [name for name in found if is_spec_violation(name)]

    return CaseScore(
        case_id=case.case_id,
        extracted=len(found),
        violations=violations,
        missing=missing,
        forbidden=forbidden,
    )


def stability(samples: list[list[str]]) -> dict[str, float]:
    """How much the answer moves when the same case is run repeatedly.

    Mean pairwise Jaccard over the extracted name sets, plus the spread
    in how many entities came back. Needs no labels, and is the metric
    that was missing when v3's counts swung between 44 and 53 with no way
    to tell noise from a real regression.

    A prompt scoring high on precision and low here is not ready: it is
    right on average and unreliable per document, and the graph is built
    one document at a time.
    """
    sets = [{normalize(n) for n in sample if normalize(n)} for sample in samples]
    counts = [len(s) for s in sets]
    if len(sets) < 2:
        return {"jaccard": 1.0, "count_mean": float(counts[0] if counts else 0), "count_stdev": 0.0}

    scores = []
    for i in range(len(sets)):
        for j in range(i + 1, len(sets)):
            union = sets[i] | sets[j]
            scores.append(len(sets[i] & sets[j]) / len(union) if union else 1.0)

    return {
        "jaccard": statistics.mean(scores),
        "count_mean": statistics.mean(counts),
        "count_stdev": statistics.pstdev(counts),
    }


@dataclass(slots=True)
class VariantResult:
    label: str
    cases: int = 0
    extracted: int = 0
    violations: int = 0
    missing: int = 0
    forbidden: int = 0
    jaccard: float = 1.0
    count_stdev: float = 0.0
    failures: int = 0

    @property
    def violation_rate(self) -> float:
        return self.violations / self.extracted if self.extracted else 0.0

    def render(self) -> str:
        return (
            f"{self.label:28} entities={self.extracted:4} "
            f"junk={self.violations:3} ({self.violation_rate:5.1%})  "
            f"missing={self.missing:3}  forbidden={self.forbidden:3}  "
            f"stability={self.jaccard:.2f}  spread={self.count_stdev:4.1f}  "
            f"failed={self.failures}"
        )


def load_cases(path: Path | str | None = None) -> list[Case]:
    source = Path(path) if path else DATASET_DIR / "entity_extraction.jsonl"
    cases: list[Case] = []
    for line in source.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("//"):
            continue
        raw = json.loads(line)
        cases.append(
            Case(
                case_id=raw["case_id"],
                text=raw["text"],
                must_include=raw.get("must_include", []),
                must_exclude=raw.get("must_exclude", []),
                note=raw.get("note"),
            )
        )
    return cases


def names_from_payload(payload: object) -> list[str]:
    """Entity names out of whatever shape the prompt version returns.

    v1 and v2 use ``name``; v3 uses ``display_name``. The harness has to
    read both or it cannot compare them.
    """
    if not isinstance(payload, dict):
        return []
    entities = payload.get("entities")
    if not isinstance(entities, list):
        return []
    names = []
    for entity in entities:
        if isinstance(entity, dict):
            name = entity.get("display_name") or entity.get("name")
            if name:
                names.append(str(name))
    return names


async def run_variant(
    cases: list[Case],
    *,
    prompt_version: str,
    model: str | None = None,
    samples: int = 3,
    label: str | None = None,
) -> VariantResult:
    """Run one prompt version (optionally on one model) over every case."""
    from contextedge.ai.prompts import _REGISTRY
    from contextedge.ai.provider import llm_complete_json

    prompt = _REGISTRY["identity"][prompt_version]
    result = VariantResult(label=label or f"{prompt_version}@{model or 'default'}")

    for case in cases:
        per_sample: list[list[str]] = []
        for _ in range(samples):
            try:
                payload = await llm_complete_json(
                    prompt.format_user(content=case.text[:6000]),
                    task="extraction",
                    model=model,
                    system_prompt=prompt.system,
                    prompt_name=prompt.name,
                    prompt_version=prompt.version,
                )
            except Exception:  # noqa: BLE001
                # A failed call is a real outcome, not a gap to skip: the
                # Vertex cache-control bug made every call with a long
                # system prompt fail, and a harness that quietly ignored
                # that would have scored the prompt as producing nothing.
                result.failures += 1
                continue
            per_sample.append(names_from_payload(payload))

        if not per_sample:
            continue

        result.cases += 1
        # Score the first sample; stability covers the rest.
        score = score_case(case, per_sample[0])
        result.extracted += score.extracted
        result.violations += len(score.violations)
        result.missing += len(score.missing)
        result.forbidden += len(score.forbidden)

        spread = stability(per_sample)
        result.jaccard = (
            (result.jaccard * (result.cases - 1) + spread["jaccard"]) / result.cases
        )
        result.count_stdev = (
            (result.count_stdev * (result.cases - 1) + spread["count_stdev"])
            / result.cases
        )

    return result
