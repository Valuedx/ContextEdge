"""Shared SQL fragments for evidence filtering invariants.

Any query that loads ``EvidenceItem`` rows for the purpose of shipping
content to an LLM, a third-party provider, or an off-tenant surface
MUST apply ``exclude_legal_hold()``. This module is the single source
of truth — don't open-code the ``sensitivity_label`` comparison in
new call sites.

Why centralise: retention already had the pattern (see
``retention_service.apply_retention_policy``), but the episode
reconstruction and contradiction scan paths silently forgot it
(review findings F-04 and F-23). Keeping the fragment here means the
next query author has a one-line import to do the right thing, and
``grep`` over ``legal_hold`` returns a short list of call sites.
"""

from __future__ import annotations

from sqlalchemy import or_
from sqlalchemy.sql.elements import ColumnElement

from contextedge.models.evidence import EvidenceItem


def exclude_legal_hold() -> ColumnElement[bool]:
    """Return a WHERE clause fragment that filters out legal-hold evidence.

    Legal-hold items have ``sensitivity_label == "legal_hold"``; anything
    else (including ``NULL``) is safe to ship to extraction paths.
    """
    return or_(
        EvidenceItem.sensitivity_label.is_(None),
        EvidenceItem.sensitivity_label != "legal_hold",
    )
