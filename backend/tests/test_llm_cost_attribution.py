"""Every LLM call must name the tenant paying for it.

``generate_embedding`` and ``generate_embeddings_batch`` skip the per-tenant
budget gate *entirely* when either ``tenant_id`` or ``db`` is missing::

    if tenant_id is not None and db is not None:
        check = await check_budget(db, tid)
        if not check.allowed and check.action == "block":
            raise TenantBudgetExceeded(check)

So an unattributed call is not merely a hole in ``/admin/cost``. It is spend a
blocked tenant can still incur, and the two failures look identical from the
outside — nobody notices until a bill arrives or a tenant that should have been
stopped was not.

This was found by reading a live log line: ``task=embedding tenant_id=unknown``,
emitted by ``search_evidence_semantic``, which already had ``db`` and
``tenant_id`` as its first two parameters and simply did not forward them.
Semantic search is the highest-frequency LLM call in the product, so that one
missing pair of keywords put runtime playbook matching, knowledge retrieval and
the copilot outside the budget block.

A scan rather than a per-call-site test, for the same reason
``test_quality_tenant_generic`` scans for product names: the defect is a class,
it has recurred, and the next occurrence should be caught here rather than in
production. If a call genuinely cannot know its tenant, add it to
``UNATTRIBUTED_ALLOWED`` with a reason — the point is that skipping the gate
becomes a decision somebody wrote down.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "contextedge"

# Functions that spend money and enforce the budget gate.
BILLED = {"generate_embedding", "generate_embeddings_batch"}

# Both are required: the gate is `tenant_id is not None and db is not None`.
REQUIRED_KWARGS = ("tenant_id", "db")

# Where the definitions and their thin wrappers live. Excluded because these
# are the functions that *receive* the kwargs, not callers that must supply
# them.
EXCLUDED_FILES = {
    SRC / "ai" / "provider.py",
    SRC / "ai" / "embeddings.py",
}

# Call sites that legitimately cannot attribute, with the reason. Empty today,
# and it should stay that way: every current caller has a tenant in scope.
UNATTRIBUTED_ALLOWED: dict[str, str] = {}


def _python_files() -> list[Path]:
    return [p for p in sorted(SRC.rglob("*.py")) if p not in EXCLUDED_FILES]


def _billed_calls(tree: ast.AST) -> list[ast.Call]:
    out: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = (
            func.id
            if isinstance(func, ast.Name)
            else func.attr
            if isinstance(func, ast.Attribute)
            else None
        )
        if name in BILLED:
            out.append(node)
    return out


def test_every_billed_embedding_call_attributes_its_tenant():
    offenders: list[str] = []
    checked = 0

    for path in _python_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - a broken file is another test's problem
            continue
        for call in _billed_calls(tree):
            checked += 1
            supplied = {kw.arg for kw in call.keywords if kw.arg}
            # `**kwargs` forwarding counts as attributed: the caller is passing
            # through whatever it was given rather than dropping it.
            forwards = any(kw.arg is None for kw in call.keywords)
            missing = [k for k in REQUIRED_KWARGS if k not in supplied]
            if missing and not forwards:
                where = f"{path.relative_to(SRC)}:{call.lineno}"
                if where in UNATTRIBUTED_ALLOWED:
                    continue
                offenders.append(f"{where}: missing {', '.join(missing)}")

    assert checked > 0, "the scan found no billed calls — it has stopped working"
    assert not offenders, (
        "these calls skip the per-tenant budget gate and bill to `unknown`:\n  "
        + "\n  ".join(offenders)
    )


@pytest.mark.parametrize(
    "path",
    [SRC / "search" / "vector_search.py", SRC / "services" / "decision_trace_service.py"],
    ids=lambda p: p.name,
)
def test_the_known_regressions_stay_fixed(path: Path):
    """Named explicitly as well as scanned.

    The scan would catch a reintroduction, but a scan failure names a line
    number and not the incident. These two files are where the live
    `tenant_id=unknown` came from.
    """
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for call in _billed_calls(tree):
        supplied = {kw.arg for kw in call.keywords if kw.arg}
        assert "tenant_id" in supplied, f"{path.name}:{call.lineno} lost tenant_id"
        assert "db" in supplied, f"{path.name}:{call.lineno} lost db"


def test_the_budget_gate_really_is_conditional_on_both():
    """Pins the premise. If the gate ever stops depending on these kwargs, this
    whole test file is guarding nothing and should be deleted rather than left
    to look like protection."""
    provider = (SRC / "ai" / "provider.py").read_text(encoding="utf-8")
    assert "if tenant_id is not None and db is not None:" in provider
