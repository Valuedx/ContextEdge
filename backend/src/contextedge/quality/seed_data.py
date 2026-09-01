"""Load quality seed/routing data from JSON files (not Python literals).

Product vocabulary and policy rules are **tenant data**. Defaults are empty
templates; optional ``examples/<profile>/`` packs are for specific deployments
only and are never loaded unless explicitly requested.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

def _find_data_dir() -> Path:
    candidates = [
        Path(__file__).resolve().parents[3] / "data" / "quality",
        Path("/app/data/quality"),
        Path.cwd() / "data" / "quality",
        Path.cwd() / "backend" / "data" / "quality",
        Path(__file__).resolve().parents[2] / "data" / "quality",
    ]
    for c in candidates:
        if c.is_dir():
            return c
    return candidates[0]


_DATA_DIR = _find_data_dir()
_EXAMPLES_DIR = _DATA_DIR / "examples"

_FALLBACK_DATA: dict[str, dict[str, Any]] = {
    "artifact_routing": {
        "defect_evidence_types": ["release_notes", "defect", "known_issue"],
        "informational_evidence_types": ["faq", "overview", "concept"],
    },
    "default_policy_pack": {
        "version": "tenant-template-1.0.0",
        "owner": None,
        "notes": "Generic tenant template.",
        "rules": [],
    },
    "default_ontology": {
        "version": "tenant-template-1.0.0",
        "owner": None,
        "notes": "Generic tenant template.",
        "components": [],
    },
}


def clear_quality_data_cache() -> None:
    """Drop cached JSON after on-disk edits (scripts, hot reload)."""
    load_quality_data.cache_clear()


@lru_cache(maxsize=16)
def load_quality_data(name: str) -> dict[str, Any]:
    for base in [
        _DATA_DIR,
        Path("/app/data/quality"),
        Path.cwd() / "data" / "quality",
        Path.cwd() / "backend" / "data" / "quality",
        Path(__file__).resolve().parents[3] / "data" / "quality",
    ]:
        path = base / f"{name}.json"
        if path.is_file():
            return _read_json_object(path)
    if name in _FALLBACK_DATA:
        return _FALLBACK_DATA[name]
    path = _DATA_DIR / f"{name}.json"
    return _read_json_object(path)


def list_quality_profiles() -> list[str]:
    if not _EXAMPLES_DIR.is_dir():
        return []
    return sorted(
        entry.name
        for entry in _EXAMPLES_DIR.iterdir()
        if entry.is_dir() and (entry / "policy_pack.json").is_file()
    )


def load_policy_pack_payload(
    *,
    profile: str | None = None,
    path: Path | str | None = None,
) -> dict[str, Any]:
    """Resolve policy pack JSON: explicit path > profile example > generic template."""
    if path is not None:
        return _read_json_object(Path(path))
    if profile:
        return _read_json_object(_EXAMPLES_DIR / profile / "policy_pack.json")
    return load_quality_data("default_policy_pack")


def load_ontology_payload(
    *,
    profile: str | None = None,
    path: Path | str | None = None,
) -> dict[str, Any]:
    """Resolve ontology JSON: explicit path > profile example > generic template."""
    if path is not None:
        return _read_json_object(Path(path))
    if profile:
        return _read_json_object(_EXAMPLES_DIR / profile / "ontology.json")
    return load_quality_data("default_ontology")


def _read_json_object(path: Path) -> dict[str, Any]:
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
