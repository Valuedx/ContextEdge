"""Deterministic playbook trigger-condition gate.

Levels are playbook-oriented (exact/strong/partial/unvalidated/contradicted)
and deliberately not the CI applicability ladder. The *result shape* matches
that service: level, matched_factors, differences, review_required.

Unknown keys are ignored; malformed JSONB downgrades to unvalidated.

Scoring is match-*ratio* with a coverage floor so a single vague token
cannot outrank a playbook with nine of ten precise conditions (GAP-10).
Key-aware environment comparison can produce ``contradicted`` (GAP-9/11).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from contextedge.models.playbook import Playbook, PlaybookVersion
from contextedge.services.case_frame_service import CaseFrame

APPLICABILITY_LEVELS = (
    "exact",
    "strong",
    "partial",
    "unvalidated",
    "contradicted",
)

_ENV_KEYS = frozenset(
    {
        "os",
        "target_os",
        "platform",
        "product",
        "category",
        "team",
        "environment",
        "region",
        "app",
        "application",
        "failing_component",
        "failure_mode",
        "component",
    }
)
_REQUIRE_KEYS = frozenset({"requires", "must", "all_of"})
_EXCLUDE_KEYS = frozenset({"excludes", "not_applicable_if", "conflicts_with"})
_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{1,}")
_EXACT_COVERAGE_FLOOR = 2


@dataclass(slots=True)
class ApplicabilityVerdict:
    level: str
    matched_factors: list[str] = field(default_factory=list)
    differences: list[str] = field(default_factory=list)
    review_required: bool = False
    drop: bool = False
    drop_reason: str | None = None


def evaluate_trigger_conditions(
    version: PlaybookVersion,
    frame: CaseFrame,
    *,
    playbook: Playbook | None = None,
    now: datetime | None = None,
) -> ApplicabilityVerdict:
    now = now or datetime.now(UTC)
    if playbook is not None and playbook.expiry_at is not None and playbook.expiry_at < now:
        return ApplicabilityVerdict(
            level="contradicted",
            differences=["expired"],
            review_required=True,
            drop=True,
            drop_reason="expired",
        )

    conflicts = version.conflicts
    conflicts_present = isinstance(conflicts, list) and len(conflicts) > 0
    raw = version.trigger_conditions

    contradicted = _negative_conditions(raw, frame)
    if contradicted is not None:
        return contradicted

    matched, missing, evaluated, contradiction = _match_conditions(raw, frame)
    if contradiction:
        return ApplicabilityVerdict(
            level="contradicted",
            matched_factors=matched,
            differences=missing or [contradiction],
            review_required=True,
            drop=True,
            drop_reason="environment_mismatch",
        )
    if evaluated == 0:
        verdict = ApplicabilityVerdict(
            level="unvalidated",
            review_required=True,
        )
    else:
        ratio = len(matched) / evaluated
        if not missing and evaluated >= _EXACT_COVERAGE_FLOOR:
            verdict = ApplicabilityVerdict(level="exact", matched_factors=matched)
        elif not missing:
            verdict = ApplicabilityVerdict(
                level="strong",
                matched_factors=matched,
            )
        elif ratio >= 0.7 and len(matched) >= 2:
            verdict = ApplicabilityVerdict(
                level="strong",
                matched_factors=matched,
                differences=missing,
            )
        elif matched:
            verdict = ApplicabilityVerdict(
                level="partial",
                matched_factors=matched,
                differences=missing,
                review_required=True,
            )
        else:
            verdict = ApplicabilityVerdict(
                level="unvalidated",
                differences=missing or ["no_trigger_overlap"],
                review_required=True,
            )

    if conflicts_present and verdict.level == "exact":
        verdict.level = "strong"
        verdict.differences.append("version_conflicts_present")
        verdict.review_required = True
    return verdict


def _negative_conditions(raw: Any, frame: CaseFrame) -> ApplicabilityVerdict | None:
    if not isinstance(raw, dict):
        return None
    for key, value in raw.items():
        lowered = str(key).strip().casefold()
        if lowered not in _EXCLUDE_KEYS:
            continue
        pairs = _keyed_pairs(value)
        if not pairs and isinstance(value, (list, tuple, str)):
            haystack = _haystack(frame)
            for factor in _flatten_trigger_values(value):
                tokens = [t for t in _split_factor(factor) if len(t) >= 4]
                if tokens and any(token in haystack for token in tokens):
                    return ApplicabilityVerdict(
                        level="contradicted",
                        differences=[f"excluded:{factor[:200]}"],
                        review_required=True,
                        drop=True,
                        drop_reason="excluded_condition",
                    )
            continue
        for env_key, expected in pairs:
            actual = _env_lookup(frame, env_key)
            if actual is not None and _values_match(expected, actual):
                return ApplicabilityVerdict(
                    level="contradicted",
                    differences=[f"excluded:{env_key}={expected}"],
                    review_required=True,
                    drop=True,
                    drop_reason="environment_excluded",
                )
    return None


def _match_conditions(
    raw: Any, frame: CaseFrame
) -> tuple[list[str], list[str], int, str | None]:
    matched: list[str] = []
    missing: list[str] = []
    evaluated = 0
    haystack = _haystack(frame)

    keyed, loose = _collect_requirements(raw)
    for env_key, expected in keyed:
        evaluated += 1
        actual = _env_lookup(frame, env_key)
        label = f"{env_key}={expected}"
        if actual is None:
            missing.append(label[:200])
            continue
        if _values_match(expected, actual):
            matched.append(label[:200])
        else:
            missing.append(label[:200])
            return matched, missing, evaluated, f"environment_mismatch:{label[:200]}"

    for factor in loose:
        tokens = [t for t in _split_factor(factor) if len(t) >= 4]
        if not tokens:
            continue
        evaluated += 1
        if any(token in haystack for token in tokens):
            matched.append(factor[:200])
        else:
            missing.append(factor[:200])
    return matched, missing, evaluated, None


def _collect_requirements(raw: Any) -> tuple[list[tuple[str, str]], list[str]]:
    keyed: list[tuple[str, str]] = []
    loose: list[str] = []
    if isinstance(raw, dict):
        for key, value in raw.items():
            lowered = str(key).strip().casefold()
            if lowered in _EXCLUDE_KEYS:
                continue
            if lowered in _REQUIRE_KEYS:
                keyed.extend(_keyed_pairs(value))
                if not _keyed_pairs(value):
                    loose.extend(_flatten_trigger_values(value))
                continue
            if lowered in _ENV_KEYS or _is_env_shaped(value):
                keyed.extend(_keyed_pairs({key: value}))
            else:
                loose.extend(_flatten_trigger_values(value))
    elif isinstance(raw, list):
        loose.extend(_flatten_trigger_values(raw))
    elif raw:
        loose.append(str(raw))
    return keyed, loose


def _is_env_shaped(value: Any) -> bool:
    return not isinstance(value, (dict, list, tuple)) and value is not None and str(value).strip() != ""


def _keyed_pairs(value: Any) -> list[tuple[str, str]]:
    if not isinstance(value, dict):
        return []
    pairs: list[tuple[str, str]] = []
    for key, item in value.items():
        if isinstance(item, (dict, list, tuple)):
            continue
        if item is None:
            continue
        text = str(item).strip()
        if text:
            pairs.append((str(key).strip(), text))
    return pairs


def _env_lookup(frame: CaseFrame, key: str) -> str | None:
    env = dict(frame.environment or {})
    wanted = key.strip().casefold()
    for candidate, value in env.items():
        if str(candidate).strip().casefold() == wanted and value is not None:
            text = str(value).strip()
            return text.casefold() if text else None
    if wanted in {"failing_component"} and frame.failing_component:
        return frame.failing_component.strip().casefold()
    if wanted in {"failure_mode"} and frame.failure_mode:
        return frame.failure_mode.strip().casefold()
    return None


def _values_match(expected: Any, actual: str) -> bool:
    exp = str(expected).strip().casefold()
    if not exp:
        return False
    return exp == actual or exp in actual or actual in exp


def _haystack(frame: CaseFrame) -> str:
    return " ".join(
        [
            frame.symptom_text,
            " ".join(frame.lexical_terms),
            " ".join(frame.identifier_tokens),
            " ".join(
                f"{key} {value}"
                for key, value in (frame.environment or {}).items()
                if value is not None
            ),
        ]
    ).casefold()


def _flatten_trigger_values(value: Any, budget: int = 24) -> list[str]:
    out: list[str] = []

    def walk(item: Any) -> None:
        if len(out) >= budget:
            return
        if isinstance(item, str):
            text = item.strip()
            if text:
                out.append(text)
        elif isinstance(item, dict):
            for child in item.values():
                walk(child)
        elif isinstance(item, (list, tuple)):
            for child in item:
                walk(child)

    walk(value)
    return out


def _split_factor(factor: str) -> list[str]:
    return [part.casefold() for part in re_split(factor) if part]


def re_split(factor: str) -> list[str]:
    return _TOKEN_RE.findall(factor)
