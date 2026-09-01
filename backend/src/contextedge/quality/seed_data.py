"""Load quality seed/routing data from JSON files (not Python literals)."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

_DATA_DIR = Path(__file__).resolve().parents[3] / "data" / "quality"


@lru_cache(maxsize=8)
def load_quality_data(name: str) -> dict[str, Any]:
    path = _DATA_DIR / f"{name}.json"
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def defect_evidence_types() -> frozenset[str]:
    routing = load_quality_data("artifact_routing")
    return frozenset(str(x) for x in routing.get("defect_evidence_types") or [])


def informational_evidence_types() -> frozenset[str]:
    routing = load_quality_data("artifact_routing")
    return frozenset(str(x) for x in routing.get("informational_evidence_types") or [])
