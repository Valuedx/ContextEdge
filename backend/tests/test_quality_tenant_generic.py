"""No product name may originate in code — it comes from the tenant's ontology.

Two things are pinned here.

**The scan.** A literal product name anywhere in the generic quality or
generation path is the defect: it puts one customer's vocabulary into every
other customer's prompt. The scan is the guard, because this has now been
fixed twice — once in ``ai/prompts/playbook.py`` and once in
``services/knowledge_retrieval_service.py`` — and a third occurrence would
otherwise be found by a customer rather than by CI.

**The renderer.** With no ontology the version text carries no product name at
all, which is the honest rendering. Substituting a default is exactly how the
hardcoding got there.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path

import pytest

from contextedge.services.knowledge_retrieval_service import (
    KnowledgeDocument,
    format_knowledge_block,
)
from contextedge.services.quality_policy_service import (
    PRODUCT_TERM_KIND,
    product_label_from_terms,
)

SRC = Path(__file__).resolve().parents[1] / "src" / "contextedge"

# Product vocabulary that must never appear in generic code. Extend when a
# tenant's terms show up somewhere they should not.
PRODUCT_TERMS = re.compile(
    r"automation\s?edge|process studio|activemq|zoho desk", re.IGNORECASE
)

# Paths whose *runtime* behaviour must be product-neutral. Scripts, evals and
# fixtures are excluded on purpose: a regression corpus is allowed to name the
# product it was drawn from.
# Paths whose *runtime* behaviour must be product-neutral. Scripts, evals and
# fixtures are excluded on purpose: a regression corpus is allowed to name the
# product it was drawn from.
GENERIC_TARGETS: tuple[Path, ...] = (
    SRC / "quality",
    SRC / "ai" / "prompts",
    SRC / "ai" / "generators",
    SRC / "search",
    SRC / "services" / "knowledge_retrieval_service.py",
)

# Prompt families that legitimately embed product names in *examples* inside
# docstrings — not in emitted prompt text. Allow-list with a stated reason so
# new files in ai/prompts/ are still scanned.
SCAN_ALLOWLIST: dict[str, str] = {
    "ai/prompts/identity.py": (
        "identity adjudication examples name real CI/product strings; "
        "not part of playbook generation"
    ),
}


def _iter_py_files(target: Path):
    if target.is_file():
        yield target
        return
    for path in sorted(target.rglob("*.py")):
        rel = path.relative_to(SRC).as_posix()
        if rel in SCAN_ALLOWLIST:
            continue
        yield path


def _code_lines(path: Path) -> list[tuple[int, str]]:
    """Lines that are neither comments nor inside a docstring.

    Comments are where the reasoning lives, and the reasoning legitimately
    names the product that exposed a defect. What must stay neutral is the
    text the system actually emits.
    """
    out: list[tuple[int, str]] = []
    in_doc = False
    delim = ""
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if in_doc:
            if delim in line:
                in_doc = False
            continue
        if line.startswith("#"):
            continue
        for candidate in ('"""', "'''"):
            if line.startswith(candidate):
                if not (line.count(candidate) >= 2 and len(line) > 3):
                    in_doc, delim = True, candidate
                break
        else:
            out.append((number, raw))
            continue
        if not in_doc:
            out.append((number, raw))
    return out


@pytest.mark.parametrize("target", GENERIC_TARGETS, ids=lambda p: p.name)
def test_no_product_name_in_generic_code(target: Path):
    offenders: list[str] = []
    for path in _iter_py_files(target):
        for number, line in _code_lines(path):
            if PRODUCT_TERMS.search(line):
                offenders.append(f"{path.relative_to(SRC)}:{number}: {line.strip()[:90]}")
    assert not offenders, (
        "product vocabulary belongs in the tenant's ontology, not in code:\n  "
        + "\n  ".join(offenders)
    )


def _doc(**kwargs) -> KnowledgeDocument:
    return KnowledgeDocument(
        evidence_id=uuid.uuid4(),
        title="License not visible",
        evidence_type="kb_article",
        **kwargs,
    )


def test_no_ontology_means_no_product_name_in_the_prompt():
    block = format_knowledge_block(
        [_doc(product_version="8.2.3", ticket_version="8.2.3")]
    )
    assert "PRODUCT VERSION: 8.2.3" in block
    assert not PRODUCT_TERMS.search(block)


def test_version_mismatch_reads_cleanly_without_a_product_name():
    block = format_knowledge_block([_doc(version_conflict=("8.1.0", "8.2.3"))])
    assert "this KB is for 8.1.0" in block
    assert not PRODUCT_TERMS.search(block)


def test_the_ontology_supplies_the_product_name():
    block = format_knowledge_block(
        [_doc(product_version="8.2.3", ticket_version="8.2.3")], "Acme RPA"
    )
    assert "PRODUCT VERSION: Acme RPA 8.2.3" in block


def test_two_tenants_get_their_own_product_name():
    """The point of the change, stated as a test."""
    doc = _doc(version_conflict=("8.1.0", "8.2.3"))
    assert "Acme RPA 8.1.0" in format_knowledge_block([doc], "Acme RPA")
    assert "Globex Bot 8.1.0" in format_knowledge_block([doc], "Globex Bot")


def test_product_label_comes_from_the_product_term():
    terms = [
        {"term_kind": "component", "canonical_term": "Agent"},
        {"term_kind": PRODUCT_TERM_KIND, "canonical_term": "Acme RPA"},
    ]
    assert product_label_from_terms(terms) == "Acme RPA"


def test_product_label_is_none_when_the_ontology_does_not_name_one():
    # None is a supported answer. A default here is the bug this replaces.
    component = [{"term_kind": "component", "canonical_term": "Agent"}]
    assert product_label_from_terms(component) is None
    assert product_label_from_terms([]) is None


def test_blank_product_term_is_treated_as_absent():
    blank = [{"term_kind": PRODUCT_TERM_KIND, "canonical_term": "  "}]
    assert product_label_from_terms(blank) is None
