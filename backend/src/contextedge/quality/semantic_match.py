"""Lexical support scoring for Stage C — pure, no model calls.

Validators stay synchronous, so support is token- and phrase-based with
polarity guards. Embedding comparison can register under a separate validator
when async evaluation is wired.
"""

from __future__ import annotations

from typing import Any

from contextedge.quality.claim_match import contains_phrase, overlap_ratio, tokens
from contextedge.quality.polarity import polarity_agrees


def contract_source_claims(contract: dict[str, Any] | None) -> list[tuple[str, str]]:
    """(text, kind) pairs drawn from the stored quality contract."""
    if not contract:
        return []
    out: list[tuple[str, str]] = []
    for key in (
        "required_actions",
        "preconditions",
        "required_validations",
        "rollback_obligations",
        "optional_actions",
        "success_criteria",
    ):
        for item in contract.get(key) or []:
            if isinstance(item, str) and item.strip():
                out.append((item.strip(), key))
    for key in ("observed_symptoms", "error_claims", "supported_cause_claims"):
        for item in contract.get(key) or []:
            if isinstance(item, str) and item.strip():
                out.append((item.strip(), key))
    for claim in contract.get("claims") or []:
        if not isinstance(claim, dict):
            continue
        text = str(claim.get("text") or "").strip()
        if text:
            out.append((text, str(claim.get("claim_type") or "claim")))
    return out


def contract_negative_claims(contract: dict[str, Any] | None) -> list[str]:
    if not contract:
        return []
    texts: list[str] = []
    for item in contract.get("known_failed_actions") or []:
        if isinstance(item, str) and item.strip():
            texts.append(item.strip())
    for claim in contract.get("claims") or []:
        if not isinstance(claim, dict):
            continue
        if claim.get("claim_type") == "negative_knowledge" and claim.get("text"):
            texts.append(str(claim["text"]).strip())
    return texts


def best_support_score(
    step_text: str, sources: list[tuple[str, str]]
) -> tuple[float, str | None]:
    """Highest overlap between step text and any source claim that agrees in polarity.

    Sources that forbid what the step performs are skipped here and surfaced by
    ``best_polarity_conflict`` instead. Counting them as support is how a step
    doing exactly what its source prohibits scored 1.00 — a perfect "entailed"
    for the one case the plan singles out as most dangerous (§4.2).
    """
    if not step_text or not sources:
        return 0.0, None
    best = 0.0
    best_text: str | None = None
    for text, _kind in sources:
        if not polarity_agrees(step_text, text):
            continue
        score = overlap_ratio(step_text, text)
        if score > best:
            best, best_text = score, text
        # Literal commands/paths in the source should still count when echoed.
        for fragment in _literal_fragments(text):
            if contains_phrase(step_text, fragment):
                best = max(best, 0.85)
                best_text = text
    return best, best_text


def best_polarity_conflict(
    step_text: str, sources: list[tuple[str, str]]
) -> tuple[float, str | None]:
    """Closest source that says the opposite of the step.

    High overlap *plus* opposite polarity is the strongest signal this module
    can produce without a model: near-identical wording where one sentence
    permits and the other forbids. It used to be scored as the strongest
    possible agreement.
    """
    if not step_text or not sources:
        return 0.0, None
    best = 0.0
    best_text: str | None = None
    for text, _kind in sources:
        if polarity_agrees(step_text, text):
            continue
        score = overlap_ratio(step_text, text)
        if score > best:
            best, best_text = score, text
    return best, best_text


def contradicts_negative(step_text: str, negative_claims: list[str]) -> tuple[float, str | None]:
    """Step aligns with a known-failed action from sources."""
    if not step_text or not negative_claims:
        return 0.0, None
    best = 0.0
    best_text: str | None = None
    for neg in negative_claims:
        # A step that *declines* the failed action matches its words just as
        # well as one that performs it. Without this the validator flagged
        # "Do not re-register the agent" as contradicting the known-failed
        # action "Re-register the agent" — a major finding against a step
        # doing the right thing.
        if not polarity_agrees(step_text, neg):
            continue
        score = overlap_ratio(step_text, neg)
        if score > best:
            best, best_text = score, neg
    return best, best_text


def _literal_fragments(text: str) -> list[str]:
    """Pull likely literals (paths, flags, dotted identifiers) from a claim."""
    raw = (text or "").split()
    frags: list[str] = []
    for piece in raw:
        cleaned = piece.strip(".,;:\"'()[]")
        if len(cleaned) < 4:
            continue
        if any(ch in cleaned for ch in ("/", "\\", ".", "-", "_", "=")):
            frags.append(cleaned)
    return frags[:8]


def bigram_jaccard(left: str, right: str) -> float:
    """Cheap paraphrase signal when token overlap under-counts reordering."""
    a = _bigrams(left)
    b = _bigrams(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _bigrams(text: str) -> set[str]:
    toks = sorted(tokens(text))
    if len(toks) < 2:
        return set(toks)
    return {f"{toks[i]} {toks[i + 1]}" for i in range(len(toks) - 1)}


def combined_entailment_score(step_text: str, source_text: str) -> float:
    """Blend token overlap and bigram similarity."""
    if not step_text or not source_text:
        return 0.0
    return max(overlap_ratio(step_text, source_text), bigram_jaccard(step_text, source_text))
