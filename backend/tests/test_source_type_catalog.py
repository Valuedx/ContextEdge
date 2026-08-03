"""The source-type catalog must agree with the connector registry.

These are drift guards, not feature tests. The bug they exist to prevent
had already happened twice, in opposite directions, in the same file:

- The source picker offered ``confluence``, ``sharepoint``, and
  ``exchange``. None has a connector, so choosing one created a source
  successfully and then failed at discovery with "Unknown source type" —
  a failure that surfaces minutes later, in a worker log, to someone who
  is not the person who clicked the button.
- The picker did not offer ``sapphireims`` or ``zoho_desk``, both of
  which had working, tested connectors. They were unreachable from the
  product entirely.

A hardcoded client-side list cannot detect either case. These tests can.
"""

from __future__ import annotations

import re

import pytest

from contextedge.connectors.registry import (
    source_type_catalog,
    supported_source_types,
)
from contextedge.schemas.source import SourceCreate


def _schema_accepted_types() -> set[str]:
    """The source types ``SourceCreate`` will validate, read off its regex."""
    pattern = next(
        meta.pattern
        for meta in SourceCreate.model_fields["source_type"].metadata
        if hasattr(meta, "pattern")
    )
    return set(re.sub(r"[\^$()]", "", pattern).split("|"))


def test_every_registered_connector_is_offered():
    """A connector that is registered but missing from the catalog is
    invisible in the product — exactly how Zoho Desk and SapphireIMS
    ended up unreachable."""
    catalog = {info.source_type for info in source_type_catalog()}
    missing = set(supported_source_types()) - catalog
    assert not missing, f"connectors with no catalog entry (unreachable in UI): {missing}"


def test_catalog_availability_is_computed_from_the_registry():
    """``connector_available`` must never be hand-maintained: a label
    claiming a connector that does not exist is the original bug."""
    available = set(supported_source_types())
    for info in source_type_catalog():
        if info.source_type == "local_file":
            # Upload path, not a connector — asserted separately below.
            continue
        assert info.connector_available is (info.source_type in available), (
            f"{info.source_type}: catalog says connector_available="
            f"{info.connector_available}, registry says "
            f"{info.source_type in available}"
        )


def test_types_without_connectors_are_marked_not_available():
    """These are accepted by the API but cannot sync. They may stay in
    the catalog as roadmap signal — they must not claim to work."""
    by_type = {info.source_type: info for info in source_type_catalog()}
    for source_type in ("confluence", "sharepoint", "exchange"):
        assert by_type[source_type].connector_available is False
        assert by_type[source_type].status == "planned"


def test_local_file_is_marked_manual_not_broken():
    """local_file has no connector *by design* — it is an upload path.
    Marking it 'planned' would tell users a working feature is missing."""
    info = next(i for i in source_type_catalog() if i.source_type == "local_file")
    assert info.connector_available is False
    assert info.status == "manual"


def test_catalog_covers_every_type_the_api_accepts():
    """A type the API accepts but the catalog omits can be created via
    the API and then has no UI representation at all."""
    missing = _schema_accepted_types() - {i.source_type for i in source_type_catalog()}
    assert not missing, f"accepted by SourceCreate but absent from the catalog: {missing}"


def test_catalog_claims_nothing_the_api_would_reject():
    """The inverse: offering a type that ``SourceCreate`` rejects means
    the picker shows an option that 422s on submit."""
    extra = {i.source_type for i in source_type_catalog()} - _schema_accepted_types()
    assert not extra, f"offered by the catalog but rejected by SourceCreate: {extra}"


def test_catalog_order_is_stable():
    """The picker renders in catalog order; reordering between requests
    would move options under the user's cursor."""
    assert [i.source_type for i in source_type_catalog()] == [
        i.source_type for i in source_type_catalog()
    ]


@pytest.mark.parametrize("source_type", ["zoho_desk", "sapphireims"])
def test_previously_unreachable_connectors_are_now_selectable(source_type):
    """Regression: both had working connectors and no way to select them."""
    info = next(i for i in source_type_catalog() if i.source_type == source_type)
    assert info.connector_available is True
    assert info.status == "available"
    assert info.label and not info.label.startswith(source_type)  # human label
