"""The entity-type registry must know every type production code writes.

ENTITY_TYPES is documentation-as-code, not a database constraint — nothing
stops a service writing an unregistered type, which is exactly what happened:
Zoho Desk reference enrichment shipped ``customer_account``,
``knowledge_category`` and ``topic`` while the registry knew none of them
(caught by the 2026-08-05 graph review as live ontology drift, not a
hypothetical).

This test closes the loop mechanically: it scans the source of every module
that constructs entities and asserts each literal ``entity_type=...`` value is
registered. A new connector adding an unregistered type fails here with a
message naming the file, instead of drifting silently.
"""

import re
from pathlib import Path

from contextedge.models.entity import ENTITY_TYPES

SRC = Path(__file__).resolve().parents[1] / "src" / "contextedge"

# Modules that create Entity rows or entity references. Deliberately a scan of
# source text, not imports: the drift happened in string literals, and a
# static scan catches a literal in any code path — including ones behind
# feature flags a unit test would never execute.
WRITER_GLOBS = (
    "services/*reference_service.py",
    "services/cmdb_topology_service.py",
    "services/entity_class_service.py",
)

_LITERAL = re.compile(r'entity_type\s*[=:]\s*"([a-z_]+)"')
# Tuple-style rows: ("field", "prefix", "entity_type", "edge_type",
# "source_kind") as used by zoho_desk_reference_service — the entity type is
# the third of FIVE strings. The fifth is required deliberately: a
# four-string pattern also matched ServiceNow's CI-trait field tuple
# ("manufacturer", "model", "os_name", "os_version") and reported "os_name"
# as an unregistered entity type — a false positive this test caught in its
# own first run.
_TUPLE_ROW = re.compile(
    r'\(\s*"[a-z_]+"\s*,\s*"[a-z_]+"\s*,\s*"([a-z_]+)"\s*,\s*"[a-z_]+"\s*,\s*"[a-z_]+"'
)


def _written_types() -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for pattern in WRITER_GLOBS:
        for path in SRC.glob(pattern):
            text = path.read_text(encoding="utf-8")
            types = set(_LITERAL.findall(text)) | set(_TUPLE_ROW.findall(text))
            if types:
                found[path.name] = types
    return found


def test_every_written_entity_type_is_registered():
    registered = set(ENTITY_TYPES)
    unregistered: list[str] = []
    for filename, types in _written_types().items():
        for entity_type in sorted(types - registered):
            unregistered.append(f"{filename}: {entity_type!r}")
    assert not unregistered, (
        "entity types written but missing from ENTITY_TYPES "
        f"(models/entity.py): {unregistered}"
    )


def test_the_scan_actually_sees_the_known_writers():
    """Guard the guard: if the globs or regexes rot, the main test would
    pass vacuously. The scan must keep finding the types we KNOW exist."""
    found = _written_types()
    all_types = set().union(*found.values()) if found else set()
    for expected in ("configuration_item", "assignment_group", "customer_account"):
        assert expected in all_types, (
            f"scan no longer finds {expected!r} — writer globs or regexes are stale"
        )
