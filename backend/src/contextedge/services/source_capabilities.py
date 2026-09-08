"""What each connector can supply, declared once.

ContextEdge normalizes every source into one canonical shape -- canonical
evidence types (``evidence_typing``), canonical relations (``graph/edge_types``),
canonical objects downstream. What it did not have was a canonical statement of
which parts of that shape a given connector can *reach*.

That gap is small until you ask the question H2 exists to answer. An agent
looking at an incident and finding no related change has to distinguish three
different worlds:

- no change happened near this incident,
- a change connector is syncing changes and this incident has none,
- nothing connected here can supply a change at all.

The first is a finding. The third is an absence of instrumentation, and
reporting it as the first is how a diagnosis becomes confidently wrong. Today
they are indistinguishable, because "no rows" looks the same in all three.

The five reference services already encode most of this knowledge -- but each
in its own shape, in its own module: a dict in ServiceNow, issue-link-type
branches in Jira, a tuple list in Zoho, inline literals in SapphireIMS. That is
fine for emitting edges and useless for answering "could this source ever emit
one", which is a question about the connector rather than about a record.

So capability is declared here, once, in canonical terms.

**Record kinds are derived, not repeated.** ``evidence_typing._OBJECT_TYPE_MAP``
already states which canonical evidence type each connector object becomes, and
a second copy of that knowledge would drift from the first. This module reads
it.

**Relations are declared, and a test keeps the declaration honest.** There is no
single structure to read them from, so they are written down -- and
``tests/test_source_capabilities.py`` cross-checks every declaration against the
edge types its reference service actually mentions, so a connector that gains or
loses a relation cannot silently disagree with what this module promises.

Deliberately NOT a refactor of the five reference services onto a shared
mapping. Their differences are real -- Jira decides relations from link-type
strings at runtime, ServiceNow from static reference fields -- and flattening
that into one table would either lose Jira's semantics or bend everyone else's
around them. The single source of truth needed here is the *capability*, not
the mechanism.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from contextedge.services.evidence_typing import _OBJECT_TYPE_MAP

# Canonical relations the reference layer can emit. Kept as a closed set so a
# typo in a declaration below is a test failure rather than a capability that
# silently never matches anything.
CANONICAL_RELATIONS = frozenset(
    {
        "affects_ci",
        "assigned_to_group",
        "caused_by_change",
        "remediated_by_change",
        "related_problem",
        "child_of_incident",
        "duplicate_of",
        "preceded_incident",
    }
)


@dataclass(frozen=True)
class SourceCapability:
    """What one connector can reach, in canonical terms.

    ``relations`` is what its reference service can emit. ``topology`` is
    separate because CI-to-CI dependency edges do not come from a record's
    reference fields at all -- they come from a CMDB the connector queries
    on its own, which today is ServiceNow's ``cmdb_rel_ci`` and nothing else.
    """

    relations: frozenset[str]
    topology: bool = False
    notes: str = ""

    # There is deliberately no "optional object types" field. Whether a given
    # INSTANCE exposes a table is not a property of the connector, and
    # discovery already answers it exactly -- it writes a SourceObject per
    # object type the instance actually exposes, so the absence of one is the
    # measurement. A static declaration would be a second, staler opinion
    # about the same fact, and would still be wrong for the instance that does
    # have the module installed.


CAPABILITIES: Mapping[str, SourceCapability] = MappingProxyType(
    {
        "servicenow": SourceCapability(
            relations=frozenset(
                {
                    "affects_ci",
                    "assigned_to_group",
                    "caused_by_change",
                    "remediated_by_change",
                    "related_problem",
                    "child_of_incident",
                    "preceded_incident",
                }
            ),
            topology=True,
            notes=(
                "Richest ITSM surface available. Reference fields are "
                "human-authored, so caused_by_change is an assertion someone "
                "made rather than an inference. em_alert requires ITOM Event "
                "Management, which a stock instance does not activate."
            ),
        ),
        "jira_sm": SourceCapability(
            relations=frozenset(
                {
                    "affects_ci",
                    "caused_by_change",
                    "remediated_by_change",
                    "related_problem",
                    "duplicate_of",
                }
            ),
            notes=(
                "Relations come from issue-link types resolved at runtime, not "
                "from fixed fields, so coverage depends on how a project "
                "configures its link types. No assignment_group: Jira's "
                "assignee is a person, and a team is a convention rather than "
                "a modelled object."
            ),
        ),
        "zoho_desk": SourceCapability(
            relations=frozenset({"affects_ci", "assigned_to_group"}),
            notes=(
                "A help desk, not an ITSM suite: no change management, no "
                "problem records, no CMDB. Its affects_ci comes from product "
                "and account names, which are commercial objects rather than "
                "configuration items -- close enough to correlate on, not "
                "close enough to walk a dependency from."
            ),
        ),
        "sapphireims": SourceCapability(
            relations=frozenset({"affects_ci"}),
            notes="Config-mapped contract; only asset/service references.",
        ),
        "manageengine": SourceCapability(
            relations=frozenset(),
            notes="No reference service written yet.",
        ),
        "teams": SourceCapability(
            relations=frozenset(),
            notes=(
                "Conversational. Carries no structured references; its value "
                "is message-level context, and it links through the ticket "
                "bridge rather than through reference fields."
            ),
        ),
        "gmail": SourceCapability(
            relations=frozenset(),
            notes="Conversational; links through the ticket bridge.",
        ),
        "local_file": SourceCapability(
            relations=frozenset(),
            notes="Uploaded documents; no source system to reference.",
        ),
    }
)


def record_kinds_for(source_type: str) -> frozenset[str]:
    """Canonical evidence types this connector can produce.

    Read from ``evidence_typing`` rather than restated: that map is what
    normalization actually applies, so anything declared here instead would
    be a second opinion about the same fact.
    """
    return frozenset(
        evidence_type
        for (declared_source, _object_type), evidence_type in _OBJECT_TYPE_MAP.items()
        if declared_source == source_type
    )


def object_types_for(source_type: str, evidence_type: str) -> frozenset[str]:
    """Connector object types that normalize to ``evidence_type``.

    Coverage needs this to answer the middle case: the connector can supply
    changes, but is this source actually syncing the table they come from?
    """
    return frozenset(
        object_type
        for (declared_source, object_type), mapped in _OBJECT_TYPE_MAP.items()
        if declared_source == source_type and mapped == evidence_type
    )


def capability_for(source_type: str) -> SourceCapability:
    """Never raises. An unknown connector is reported as supplying nothing,
    which is the safe reading: coverage then says 'unsupported' rather than
    assuming a capability nobody declared."""
    return CAPABILITIES.get(source_type, SourceCapability(relations=frozenset()))


def supports_relation(source_type: str, relation: str) -> bool:
    return relation in capability_for(source_type).relations


def supports_record_kind(source_type: str, evidence_type: str) -> bool:
    return evidence_type in record_kinds_for(source_type)
