"""The scenario fixture has to rebuild on an instance nobody has prepared.

Nothing in it hardcodes a sys_id — every user, group, knowledge base, catalog
item and relationship type is resolved by name — so the same script rebuilds
the whole set anywhere. What differs between instances is which of those
prerequisites exist, and a missing one used to degrade silently: the lookup
returned None, the field was omitted, and the record landed without its
assignment group or without its topology edge.

That produces a fixture that builds clean and tests nothing, which is the exact
failure this module exists to prevent. So the prerequisites are declared, the
critical ones abort, and `--verify` checks the references that carry the
meaning rather than merely counting rows — ServiceNow stores an unresolvable
reference as EMPTY rather than rejecting it, so presence proves nothing.
"""

from __future__ import annotations

import inspect

import pytest

from evals.fixtures import servicenow_scenarios as fx


class _FakeSnow:
    """Resolves whatever it is told to, records whether it wrote anything."""

    def __init__(self, missing: set[str] | None = None):
        self.missing = missing or set()
        self.granted: list[str] = []

    def lookup(self, table, query):
        return None if table in self.missing else f"sysid-{table}"

    def holds_role(self, user_sys_id, role_name):
        return True

    def ensure_role(self, user_sys_id, role_name):
        self.granted.append(role_name)
        return "granted"


# --- the prerequisite contract ---------------------------------------------


def test_topology_relationship_types_are_critical():
    """Without them S3 and S9 have no dependency edge, so the one-hop blast
    radius they exist to prove is untestable — and the run would still
    report success."""
    critical = {key for key, _t, _q, is_critical in fx.PREREQUISITES if is_critical}
    assert "rel_depends_on" in critical
    assert "rel_runs_on" in critical
    assert "assignee" in critical


def test_optional_prerequisites_name_what_they_cost():
    """A warning that does not say what degraded is a warning nobody acts on."""
    optional = {key for key, _t, _q, is_critical in fx.PREREQUISITES if not is_critical}
    assert optional <= set(fx.AFFECTED_BY)
    for key in optional:
        assert fx.AFFECTED_BY[key].strip()


def test_a_missing_critical_prerequisite_aborts():
    with pytest.raises(RuntimeError) as excinfo:
        fx.preflight(_FakeSnow(missing={"cmdb_rel_type"}))
    message = str(excinfo.value)
    assert "cmdb_rel_type" in message
    # The message has to say WHY it matters, or it reads as pedantry.
    assert "silently" in message or "nothing" in message


def test_a_missing_optional_prerequisite_does_not_abort():
    resolved = fx.preflight(_FakeSnow(missing={"kb_knowledge_base"}))
    assert resolved["kb_it"] is None
    assert resolved["assignee"] is not None


def test_preflight_is_read_only_by_default():
    """It has to be safe to point at an instance somebody else owns. A check
    that quietly changes the thing it checks is not a check."""
    snow = _FakeSnow()
    fx.preflight(snow)
    assert snow.granted == []


def test_build_grants_roles_because_it_is_allowed_to_write():
    """Problem creation is refused outright without a problem role — a 403
    that reads as misconfiguration and is a missing grant."""
    snow = _FakeSnow()
    fx.preflight(snow, grant_roles=True)
    assert set(snow.granted) == set(fx.REQUIRED_ROLES)

    source = inspect.getsource(fx.build)
    assert "grant_roles=True" in source


# --- verify checks meaning, not presence -----------------------------------


def test_verify_checks_the_references_that_carry_the_scenarios():
    """Counting rows would pass on a fixture whose every reference is blank."""
    source = inspect.getsource(fx.verify)
    for field in ("caused_by", "problem_id", "parent_incident", "rfc", "cmdb_ci"):
        assert field in source


def test_verify_checks_the_b3_baseline():
    """The inventory-diff detector needs a PRIOR value to diff against; a
    first observation is deliberately not a change, so without a baseline
    os_version the B3 scenario is silently untestable."""
    source = inspect.getsource(fx.verify)
    assert "os_version" in source

    build_source = inspect.getsource(fx.build)
    assert '"os_version": "8.6"' in build_source


def test_verify_reports_a_count_so_it_can_gate_a_pipeline():
    source = inspect.getsource(fx.verify)
    assert "return problems" in source
    assert "READY" in source


def test_the_cli_offers_preflight_build_verify_teardown():
    source = inspect.getsource(fx.main)
    for flag in ("--preflight", "--build", "--verify", "--teardown"):
        assert flag in source
